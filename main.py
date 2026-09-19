from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core import (
    Deduplicator,
    FileListener,
    OneBotFileAdapter,
    TelegramFileAdapter,
    build_listener_options,
    create_direct_link_binding,
    logger,
)


class FileListenerPlugin(Star):
    """Telegram / OneBot 文件事件监听插件入口。"""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | dict | None = None,
    ) -> None:
        super().__init__(context, config)
        self.context = context
        self.config = config or {}
        self._listener: FileListener | None = None
        self._deduplicator: Deduplicator | None = None
        self._adapters: dict[str, object] = {}
        self._onebot_raw_hooks: list[tuple[Any, Any, Any]] = []

    async def initialize(self) -> None:
        """创建当前插件生命周期内的 listener、adapter 与去重器。"""
        options = build_listener_options(self.config)
        listener = FileListener(options)
        listener.register([create_direct_link_binding(options)])

        self._listener = listener
        self._deduplicator = Deduplicator(
            enabled=options.dedupe_enabled,
            window_seconds=options.dedupe_window_seconds,
        )
        self._adapters = {
            "telegram": TelegramFileAdapter(),
            "aiocqhttp": OneBotFileAdapter(),
        }
        if not options.ignore_self_messages:
            self._install_onebot_self_message_hooks()
        logger.info("文件监听器初始化完成")

    def get_file_listener(self) -> FileListener:
        """返回当前插件生命周期内的公开 listener 实例。

        Returns:
            已初始化的 FileListener。

        Raises:
            RuntimeError: 插件尚未初始化或已经卸载。
        """
        if self._listener is None:
            raise RuntimeError("File Listener 尚未初始化或已经卸载")
        return self._listener

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP | filter.PlatformAdapterType.TELEGRAM
    )
    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE
    )
    async def on_file_event(self, event: AstrMessageEvent) -> None:
        """规范化、去重并分发 AstrBot 文件事件。"""
        listener = self._listener
        deduplicator = self._deduplicator
        if listener is None or deduplicator is None:
            return

        if listener.options.ignore_self_messages:
            get_self_id = getattr(event, "get_self_id", None)
            self_id = get_self_id() if callable(get_self_id) else None
            sender_id = event.get_sender_id()
            if self_id and sender_id and str(self_id) == str(sender_id):
                return

        platform = event.get_platform_name()
        adapter = self._adapters.get(platform)
        if adapter is None:
            return

        try:
            source = adapter.classify(event)
        except Exception:
            logger.exception("文件事件来源分类失败: platform=%s", platform)
            return
        if source is None:
            return

        source_group = "onebot" if platform == "aiocqhttp" else platform
        enabled_sources = listener.options.monitor_sources.get(
            source_group, frozenset()
        )
        if source not in enabled_sources:
            return

        try:
            batch = await adapter.extract(event, source, listener.options)
        except Exception:
            logger.exception(
                "文件事件平台解析失败: platform=%s source=%s",
                platform,
                source,
            )
            return
        if batch is None:
            return
        await self._dispatch_batch(batch)

    async def _dispatch_batch(self, batch) -> None:
        listener = self._listener
        deduplicator = self._deduplicator
        if listener is None or deduplicator is None:
            return

        source_group = "onebot" if batch.platform == "aiocqhttp" else batch.platform
        enabled_sources = listener.options.monitor_sources.get(
            source_group, frozenset()
        )
        if batch.source_type not in enabled_sources:
            return
        if deduplicator.is_duplicate(batch):
            logger.debug(
                "跳过重复文件事件: platform=%s source=%s event_id=%s",
                batch.platform,
                batch.source_type,
                batch.event_id,
            )
            return
        await listener.dispatch(batch)

    def _install_onebot_self_message_hooks(self) -> None:
        """为 aiocqhttp 1.x 丢弃的 message_sent 安装私有兼容 hook。"""
        manager = getattr(self.context, "platform_manager", None)
        get_insts = getattr(manager, "get_insts", None)
        if callable(get_insts):
            platforms = get_insts()
        else:
            platforms = getattr(manager, "platform_insts", []) if manager else []

        installed = 0
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            if getattr(meta, "name", None) != "aiocqhttp":
                continue
            client = platform.get_client()
            original = getattr(client, "_handle_event", None)
            if not callable(original):
                logger.warning(
                    "aiocqhttp client 不提供 raw event handler，无法监听 Bot 自身消息"
                )
                continue
            if getattr(client, "_astrbot_file_listener_self_hook", None) is not None:
                logger.warning(
                    "aiocqhttp client 已存在 File Listener 自身消息 hook，跳过"
                )
                continue

            platform_id = str(getattr(meta, "id", "aiocqhttp"))

            async def wrapped(
                payload,
                *,
                _original=original,
                _client=client,
                _platform_id=platform_id,
            ):
                if (
                    isinstance(payload, Mapping)
                    and payload.get("post_type") == "message_sent"
                ):
                    await self._handle_onebot_self_message_payload(
                        payload,
                        platform_id=_platform_id,
                        client=_client,
                    )
                    return None
                return await _original(payload)

            # aiocqhttp 1.x 会在 Event.from_payload() 前丢弃 message_sent，且没有
            # public raw-payload hook；这里只对当前 client 实例做窄范围兼容。
            setattr(client, "_handle_event", wrapped)
            setattr(client, "_astrbot_file_listener_self_hook", wrapped)
            self._onebot_raw_hooks.append((client, original, wrapped))
            installed += 1

        if installed:
            logger.info(
                "已启用 OneBot 自身消息监听兼容层；上游仍需 reportSelfMessage=true"
            )
        else:
            logger.warning(
                "未找到可挂载的 aiocqhttp client；OneBot 自身消息暂时无法监听"
            )

    async def _handle_onebot_self_message_payload(
        self,
        payload: Mapping[str, Any],
        *,
        platform_id: str,
        client: Any,
    ) -> None:
        listener = self._listener
        adapter = self._adapters.get("aiocqhttp")
        if listener is None or not isinstance(adapter, OneBotFileAdapter):
            return
        if listener.options.ignore_self_messages:
            return
        message_type = payload.get("message_type")
        source = "private_file" if message_type == "private" else "message"
        if source not in listener.options.monitor_sources.get("onebot", frozenset()):
            return
        try:
            batch = await adapter.extract_self_message_payload(
                payload,
                platform_id=platform_id,
                client=client,
            )
        except Exception:
            logger.exception("OneBot 自身文件消息解析失败")
            return
        if batch is not None:
            await self._dispatch_batch(batch)

    def _remove_onebot_self_message_hooks(self) -> None:
        for client, original, wrapped in self._onebot_raw_hooks:
            if getattr(client, "_handle_event", None) is wrapped:
                setattr(client, "_handle_event", original)
            if getattr(client, "_astrbot_file_listener_self_hook", None) is wrapped:
                try:
                    delattr(client, "_astrbot_file_listener_self_hook")
                except AttributeError:
                    pass
        self._onebot_raw_hooks.clear()

    async def terminate(self) -> None:
        """释放当前插件实例持有的全部生命周期状态。"""
        self._remove_onebot_self_message_hooks()
        if self._listener is not None:
            self._listener.close()
        if self._deduplicator is not None:
            self._deduplicator.clear()
        self._listener = None
        self._deduplicator = None
        self._adapters.clear()
        logger.info("文件监听器已卸载")
