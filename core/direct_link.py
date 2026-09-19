from __future__ import annotations

from string import Formatter

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from .listener import FilterChain
from .logger import logger
from .models import (
    CallbackBinding,
    FileEvent,
    FilterContext,
    FilterSpec,
    ListenerOptions,
)

DEFAULT_DIRECT_LINK_TEMPLATE = (
    "文件名：{file_name}\n大小：{file_size}\n链接：{file_url}"
)
_ALLOWED_FIELDS = {"file_name", "file_size", "file_url"}


def validate_direct_link_template(template: str) -> None:
    """校验直链模板只使用公开支持的变量。

    Args:
        template: 待校验模板。

    Raises:
        ValueError: 模板包含未知变量或不受支持的格式表达式。
    """
    try:
        fields = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError(f"直链模板格式无效: {exc}") from exc
    used_fields: set[str] = set()
    for _, field_name, format_spec, conversion in fields:
        if field_name is None:
            continue
        if field_name not in _ALLOWED_FIELDS:
            raise ValueError(f"未知模板变量: {field_name}")
        if format_spec or conversion:
            raise ValueError("直链模板不支持 format spec 或 conversion")
        used_fields.add(field_name)

    missing = {"file_name", "file_url"} - used_fields
    if missing:
        raise ValueError("直链模板必须包含: " + ", ".join(sorted(missing)))


def render_file_template(template: str, file_event: FileEvent) -> str:
    """渲染单个文件的直链文本。

    Args:
        template: 已配置的直链模板。
        file_event: 当前文件事件。

    Returns:
        渲染后的消息文本。
    """
    validate_direct_link_template(template)
    lines = template.splitlines()
    if file_event.file_size is None:
        lines = [line for line in lines if "{file_size}" not in line]
    return "\n".join(lines).format(
        file_name=file_event.file_name,
        file_size=file_event.file_size if file_event.file_size is not None else "",
        file_url=file_event.file_url or "",
    )


class SendDirectLinkFilter:
    """DirectLink binding 固定的第一个副作用过滤器。"""

    def __init__(self, options: ListenerOptions):
        self._options = options

    async def __call__(self, context: FilterContext) -> FilterContext:
        """立即把当前 batch 中可发送的文件直链回复到源会话。"""
        if not self._options.send_direct_link:
            return context

        rendered = [
            render_file_template(self._options.direct_link_template, file_event)
            for file_event in context.current_files
            if isinstance(file_event.file_url, str)
            and file_event.file_url.startswith(("http://", "https://"))
        ]
        if not rendered:
            logger.debug("文件事件没有可直接发送的 URL，跳过 DirectLink")
            return context

        if self._options.file_reply_mode == "separate":
            messages = rendered
        elif self._options.file_reply_mode in {"smart", "aggregate"}:
            messages = ["\n\n".join(rendered)]
        else:
            logger.warning(
                "未知 file_reply_mode=%s，按 smart 处理",
                self._options.file_reply_mode,
            )
            messages = ["\n\n".join(rendered)]

        raw_event = context.original_batch.raw_event
        send = getattr(raw_event, "send", None)
        if not callable(send):
            logger.warning("当前文件事件不提供 send()，无法发送 DirectLink")
            return context
        for message in messages:
            await send(MessageChain(chain=[Plain(message)]))
        return context


class _DirectLinkFilterChain(FilterChain):
    """保证系统 DirectLink filter 永远先于普通 priority filters。"""

    def __init__(
        self,
        options: ListenerOptions,
        filters: list[FilterSpec] | tuple[FilterSpec, ...] = (),
    ):
        super().__init__(filters)
        self._system_filter = SendDirectLinkFilter(options)

    async def run(self, context: FilterContext) -> FilterContext:
        returned = await self._system_filter(context)
        if returned is not context:
            raise RuntimeError("SendDirectLinkFilter 必须返回原 FilterContext")
        return await super().run(context)


def create_direct_link_binding(
    options: ListenerOptions,
    *,
    filters: list[FilterSpec] | tuple[FilterSpec, ...] = (),
) -> CallbackBinding:
    """创建插件内置 DirectLink CallbackBinding。"""

    async def terminal_callback(batch, callback_options) -> None:
        del batch, callback_options

    return CallbackBinding(
        callback=terminal_callback,
        filter_chain=_DirectLinkFilterChain(options, filters),
    )
