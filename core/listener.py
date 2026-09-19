from __future__ import annotations

import asyncio
import inspect
import weakref
from collections.abc import Hashable
from dataclasses import replace

from .logger import logger
from .models import (
    CallbackBinding,
    FileEventBatch,
    FilterContext,
    FilterSpec,
    ListenerOptions,
)


def _is_async_callable(callback: object) -> bool:
    """判断可调用对象是否声明为异步调用。"""
    if inspect.iscoroutinefunction(callback):
        return True
    call = getattr(callback, "__call__", None)
    return inspect.iscoroutinefunction(call)


def _callback_key(callback: object) -> Hashable:
    """生成稳定 callback 身份，兼容重复访问 bound method 的场景。"""
    owner = getattr(callback, "__self__", None)
    function = getattr(callback, "__func__", None)
    if owner is not None and function is not None:
        return ("bound", id(owner), function)
    return ("callable", id(callback))


class FilterChain:
    """顺序执行一组共享同一 FilterContext 的异步过滤器。"""

    def __init__(self, filters: list[FilterSpec] | tuple[FilterSpec, ...]):
        specs = list(filters)
        for spec in specs:
            if not isinstance(spec, FilterSpec):
                raise TypeError("FilterChain 只接受 FilterSpec")
            if not _is_async_callable(spec.callback):
                raise TypeError("filter 必须是 async callable")
        self._filters = tuple(sorted(specs, key=lambda spec: spec.priority))

    async def run(self, context: FilterContext) -> FilterContext:
        """在同一个上下文实例上运行过滤器链。

        Args:
            context: 当前 binding 独占的过滤上下文。

        Returns:
            传入的同一个 FilterContext 实例。

        Raises:
            RuntimeError: filter 返回了新的上下文实例。
        """
        for spec in self._filters:
            if not context.continue_chain:
                break
            returned = await spec.callback(context)
            if returned is not context:
                raise RuntimeError("filter 必须返回传入的同一个 FilterContext 实例")
        return context


class RegistrationHandle:
    """一批 callback 注册的幂等注销句柄。"""

    def __init__(self, listener: FileListener, keys: tuple[Hashable, ...]):
        self._listener_ref = weakref.ref(listener)
        self._keys = keys
        self._active = True

    @property
    def active(self) -> bool:
        """返回该句柄是否仍代表有效注册。"""
        listener = self._listener_ref()
        return self._active and listener is not None and not listener.closed

    def unregister(self) -> None:
        """注销该句柄对应的全部 bindings；重复调用无副作用。"""
        if not self._active:
            return
        self._active = False
        listener = self._listener_ref()
        if listener is not None:
            listener._unregister(self._keys)

    def _invalidate(self) -> None:
        self._active = False


class FileListener:
    """管理 callback bindings 并分发规范化文件事件。"""

    def __init__(self, options: ListenerOptions):
        self.options = options
        self._bindings: dict[Hashable, CallbackBinding] = {}
        self._handles: weakref.WeakSet[RegistrationHandle] = weakref.WeakSet()
        self.closed = False

    def register(self, bindings: list[CallbackBinding]) -> RegistrationHandle:
        """原子注册一批 callback bindings。

        Args:
            bindings: 待注册的 callback bindings。

        Returns:
            可幂等注销整批注册的 RegistrationHandle。

        Raises:
            RuntimeError: listener 已关闭。
            TypeError: binding、callback 或 filter chain 不合法。
            ValueError: callback 在当前 listener 生命周期中重复注册。
        """
        if self.closed:
            raise RuntimeError("FileListener 已关闭")

        prepared: list[tuple[Hashable, CallbackBinding]] = []
        seen: set[Hashable] = set()
        for binding in bindings:
            if not isinstance(binding, CallbackBinding):
                raise TypeError("register() 只接受 CallbackBinding")
            if not _is_async_callable(binding.callback):
                raise TypeError("callback 必须是 async callable")
            if binding.filter_chain is not None and not isinstance(
                binding.filter_chain, FilterChain
            ):
                raise TypeError("filter_chain 必须是 FilterChain 或 None")
            key = _callback_key(binding.callback)
            if key in seen or key in self._bindings:
                raise ValueError("同一个 callback 不能重复注册")
            seen.add(key)
            prepared.append((key, binding))

        for key, binding in prepared:
            self._bindings[key] = binding

        handle = RegistrationHandle(self, tuple(key for key, _ in prepared))
        self._handles.add(handle)
        return handle

    async def dispatch(self, batch: FileEventBatch) -> None:
        """把一个源事件批次分发给当前所有 callback bindings。"""
        if self.closed:
            return
        bindings = tuple(self._bindings.values())
        if self.options.parallel:
            await asyncio.gather(*(self._run_binding(binding, batch) for binding in bindings))
            return
        for binding in bindings:
            await self._run_binding(binding, batch)

    async def _run_binding(
        self, binding: CallbackBinding, batch: FileEventBatch
    ) -> None:
        try:
            context = FilterContext.from_batch(batch)
            if binding.filter_chain is not None:
                await binding.filter_chain.run(context)
            if not context.current_files:
                return
            filtered_batch = replace(batch, files=tuple(context.current_files))
            await binding.callback(filtered_batch, self.options)
        except Exception:
            logger.exception("文件监听 callback binding 执行失败")

    def _unregister(self, keys: tuple[Hashable, ...]) -> None:
        for key in keys:
            self._bindings.pop(key, None)

    def close(self) -> None:
        """关闭 listener 并清理全部生命周期内注册状态。"""
        if self.closed:
            return
        self.closed = True
        self._bindings.clear()
        for handle in tuple(self._handles):
            handle._invalidate()
        self._handles.clear()
