from __future__ import annotations

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

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_file_event(self, event: AstrMessageEvent) -> None:
        """规范化、去重并分发 AstrBot 文件事件。"""
        listener = self._listener
        deduplicator = self._deduplicator
        if listener is None or deduplicator is None:
            return

        platform = event.get_platform_name()
        adapter = self._adapters.get(platform)
        if adapter is None:
            return

        source = adapter.classify(event)
        if source is None:
            return

        source_group = "onebot" if platform == "aiocqhttp" else platform
        enabled_sources = listener.options.monitor_sources.get(
            source_group, frozenset()
        )
        if source not in enabled_sources:
            return

        batch = await adapter.extract(event, source, listener.options)
        if batch is None:
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

    async def terminate(self) -> None:
        """释放当前插件实例持有的全部生命周期状态。"""
        if self._listener is not None:
            self._listener.close()
        if self._deduplicator is not None:
            self._deduplicator.clear()
        self._listener = None
        self._deduplicator = None
        self._adapters.clear()
        logger.info("文件监听器已卸载")
