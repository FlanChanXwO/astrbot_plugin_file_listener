from __future__ import annotations

import inspect
from typing import Any

from astrbot.api.message_components import File

from ..logger import logger
from ..models import FileEvent, FileEventBatch, ListenerOptions


class TelegramFileAdapter:
    """把 AstrBot Telegram 文件消息规范化为 FileEventBatch。"""

    def classify(self, event: Any) -> str | None:
        """对 Telegram 文件事件做廉价来源分类。"""
        if event.get_platform_name() != "telegram":
            return None
        if not any(isinstance(component, File) for component in event.get_messages()):
            return None

        raw_update = getattr(event.message_obj, "raw_message", None)
        raw_message = getattr(raw_update, "message", None)
        if raw_message is not None and any(
            getattr(raw_message, field, None) is not None
            for field in (
                "forward_origin",
                "forward_from",
                "forward_from_chat",
                "forward_sender_name",
            )
        ):
            return "forwarded"
        return "message"

    async def extract(
        self,
        event: Any,
        source: str | None,
        options: ListenerOptions,
    ) -> FileEventBatch | None:
        """从已分类 Telegram 事件提取文件批次。"""
        del options
        if source not in {"message", "forwarded"}:
            return None

        components = [
            component
            for component in event.get_messages()
            if isinstance(component, File)
        ]
        if not components:
            return None

        raw_update = getattr(event.message_obj, "raw_message", None)
        raw_message = getattr(raw_update, "message", None)
        document = getattr(raw_message, "document", None)
        files: list[FileEvent] = []
        for index, component in enumerate(components):
            raw_document = document if index == 0 else None
            component_url = getattr(component, "url", None)
            file_url = (
                component_url
                if isinstance(component_url, str)
                and component_url.startswith(("http://", "https://"))
                else None
            )
            file_value = getattr(component, "file_", None)
            if (
                not file_url
                and isinstance(file_value, str)
                and file_value.startswith(("http://", "https://"))
            ):
                file_url = file_value

            if not file_url and raw_document is not None:
                get_file = getattr(raw_document, "get_file", None)
                if callable(get_file):
                    try:
                        telegram_file = get_file()
                        if inspect.isawaitable(telegram_file):
                            telegram_file = await telegram_file
                        candidate = getattr(telegram_file, "file_path", None)
                        if isinstance(candidate, str) and candidate.startswith(
                            ("http://", "https://")
                        ):
                            file_url = candidate
                    except Exception:
                        logger.warning("Telegram 文件 URL 补齐失败", exc_info=True)

            raw_size = getattr(raw_document, "file_size", None)
            try:
                file_size = int(raw_size) if raw_size is not None else None
            except (TypeError, ValueError):
                file_size = None

            files.append(
                FileEvent(
                    file_name=(
                        getattr(component, "name", None)
                        or getattr(raw_document, "file_name", None)
                        or "file"
                    ),
                    file_url=file_url,
                    file_id=getattr(raw_document, "file_id", None),
                    file_size=file_size,
                    platform="telegram",
                    chat_type="private" if event.is_private_chat() else "group",
                    source_type=source,
                    chat_id=(
                        event.get_session_id()
                        if event.is_private_chat()
                        else event.get_group_id()
                    )
                    or None,
                    sender_id=event.get_sender_id() or None,
                    raw_file=raw_document or component,
                )
            )

        raw_event_id = getattr(event.message_obj, "message_id", None)
        return FileEventBatch(
            files=tuple(files),
            raw_event=event,
            platform="telegram",
            source_type=source,
            event_id=str(raw_event_id) if raw_event_id not in {None, ""} else None,
            platform_id=event.get_platform_id() or None,
        )
