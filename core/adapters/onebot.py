from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from astrbot.api.message_components import File, Forward

from ..logger import logger
from ..models import FileEvent, FileEventBatch, ListenerOptions


class OneBotFileAdapter:
    """把 AstrBot aiocqhttp/OneBot 文件事件规范化为 FileEventBatch。"""

    @staticmethod
    def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    @staticmethod
    def _fill_blank_fname(file_url: str | None, file_name: str) -> str | None:
        if not file_url:
            return None
        encoded_name = quote(file_name, safe="")
        return re.sub(
            r"([?&]fname=)(?=&|#|$)",
            lambda match: match.group(1) + encoded_name,
            file_url,
            count=1,
        )

    def classify(self, event: Any) -> str | None:
        """在执行 OneBot action 前完成廉价来源分类。"""
        if event.get_platform_name() != "aiocqhttp":
            return None

        raw = getattr(event.message_obj, "raw_message", None)
        if (
            self._raw_get(raw, "post_type") == "notice"
            and self._raw_get(raw, "notice_type") == "group_upload"
        ):
            return "group_upload_notice"

        messages = event.get_messages()
        if any(isinstance(component, Forward) for component in messages):
            return "merged_forward"
        if any(isinstance(component, File) for component in messages):
            return "private_file" if event.is_private_chat() else "message"
        return None

    async def extract(
        self,
        event: Any,
        source: str | None,
        options: ListenerOptions,
    ) -> FileEventBatch | None:
        """提取指定 OneBot 来源中的全部文件。"""
        if source == "group_upload_notice":
            files = await self._extract_upload_notice(event)
        elif source == "merged_forward":
            files = await self._extract_merged_forward(event, options.forward_max_depth)
        elif source in {"message", "private_file"}:
            files = await self._extract_file_components(event, source)
        else:
            return None

        if not files:
            return None

        raw_event_id = self._raw_get(
            getattr(event.message_obj, "raw_message", None), "message_id"
        )
        if raw_event_id in {None, ""}:
            raw_event_id = getattr(event.message_obj, "message_id", None)
        return FileEventBatch(
            files=tuple(files),
            raw_event=event,
            platform="aiocqhttp",
            source_type=source,
            event_id=str(raw_event_id) if raw_event_id not in {None, ""} else None,
            platform_id=event.get_platform_id() or None,
        )

    async def _extract_file_components(
        self, event: Any, source: str
    ) -> list[FileEvent]:
        components = [
            component
            for component in event.get_messages()
            if isinstance(component, File)
        ]
        raw = getattr(event.message_obj, "raw_message", None)
        raw_segments = self._raw_get(raw, "message")
        if not isinstance(raw_segments, list):
            raw_segments = []
        raw_files = [
            segment
            for segment in raw_segments
            if isinstance(segment, Mapping) and segment.get("type") == "file"
        ]

        files: list[FileEvent] = []
        for index, component in enumerate(components):
            raw_segment = raw_files[index] if index < len(raw_files) else None
            data = (
                raw_segment.get("data", {}) if isinstance(raw_segment, Mapping) else {}
            )
            if not isinstance(data, Mapping):
                data = {}
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
            if not file_url:
                candidate = data.get("url")
                if isinstance(candidate, str) and candidate.startswith(
                    ("http://", "https://")
                ):
                    file_url = candidate

            files.append(
                self._build_file_event(
                    event,
                    source,
                    file_name=(
                        getattr(component, "name", None)
                        or data.get("file_name")
                        or data.get("name")
                        or data.get("file")
                        or "file"
                    ),
                    file_url=file_url,
                    file_id=data.get("file_id") or data.get("id"),
                    file_size=self._first_present(data, "file_size", "size"),
                    raw_file=raw_segment or component,
                )
            )
        return files

    async def _extract_upload_notice(self, event: Any) -> list[FileEvent]:
        raw = getattr(event.message_obj, "raw_message", None)
        file_data = self._raw_get(raw, "file")
        if not isinstance(file_data, Mapping):
            return []
        file_id = file_data.get("id") or file_data.get("file_id")
        file_url = file_data.get("url")
        if not isinstance(file_url, str) or not file_url.startswith(
            ("http://", "https://")
        ):
            file_url = None
        if file_url is None and file_id:
            file_url = await self._resolve_file_url(event, str(file_id))

        return [
            self._build_file_event(
                event,
                "group_upload_notice",
                file_name=(
                    file_data.get("name")
                    or file_data.get("file_name")
                    or file_data.get("file")
                    or "file"
                ),
                file_url=file_url,
                file_id=file_id,
                file_size=self._first_present(file_data, "size", "file_size"),
                raw_file=file_data,
            )
        ]

    async def _extract_merged_forward(
        self, event: Any, max_depth: int
    ) -> list[FileEvent]:
        files = await self._extract_file_components(event, "merged_forward")
        for component in event.get_messages():
            if not isinstance(component, Forward):
                continue
            forward_id = getattr(component, "id", None)
            if forward_id:
                await self._collect_forward_files(
                    event,
                    str(forward_id),
                    depth=1,
                    max_depth=max_depth,
                    output=files,
                )
        return files

    async def _collect_forward_files(
        self,
        event: Any,
        forward_id: str,
        *,
        depth: int,
        max_depth: int,
        output: list[FileEvent],
    ) -> None:
        if depth > max_depth:
            return
        payload = await self._get_forward_msg(event, forward_id)
        if not isinstance(payload, Mapping):
            return
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return

        for node in messages:
            if not isinstance(node, Mapping):
                continue
            segments = node.get("message") or node.get("content")
            if not isinstance(segments, list):
                if node.get("type"):
                    segments = [node]
                else:
                    continue
            for segment in segments:
                if not isinstance(segment, Mapping):
                    continue
                segment_type = segment.get("type")
                data = segment.get("data", {})
                if not isinstance(data, Mapping):
                    data = {}
                if segment_type == "file":
                    file_id = data.get("file_id") or data.get("id")
                    file_url = data.get("url")
                    if not isinstance(file_url, str) or not file_url.startswith(
                        ("http://", "https://")
                    ):
                        file_url = None
                    if file_url is None and file_id:
                        file_url = await self._resolve_file_url(event, str(file_id))
                    output.append(
                        self._build_file_event(
                            event,
                            "merged_forward",
                            file_name=(
                                data.get("file_name")
                                or data.get("name")
                                or data.get("file")
                                or "file"
                            ),
                            file_url=file_url,
                            file_id=file_id,
                            file_size=self._first_present(data, "file_size", "size"),
                            raw_file=segment,
                        )
                    )
                elif segment_type == "forward" and depth < max_depth:
                    nested_id = data.get("id") or data.get("message_id")
                    if nested_id:
                        await self._collect_forward_files(
                            event,
                            str(nested_id),
                            depth=depth + 1,
                            max_depth=max_depth,
                            output=output,
                        )

    def _build_file_event(
        self,
        event: Any,
        source: str,
        *,
        file_name: object,
        file_url: str | None,
        file_id: object,
        file_size: object,
        raw_file: object,
    ) -> FileEvent:
        parsed_name = str(file_name)
        try:
            parsed_size = int(file_size) if file_size is not None else None
        except (TypeError, ValueError):
            parsed_size = None
        return FileEvent(
            file_name=parsed_name,
            file_url=self._fill_blank_fname(file_url, parsed_name),
            file_id=str(file_id) if file_id not in {None, ""} else None,
            file_size=parsed_size,
            platform="aiocqhttp",
            chat_type="private" if event.is_private_chat() else "group",
            source_type=source,
            chat_id=(
                event.get_session_id()
                if event.is_private_chat()
                else event.get_group_id()
            )
            or None,
            sender_id=event.get_sender_id() or None,
            raw_file=raw_file,
        )

    async def _resolve_file_url(self, event: Any, file_id: str) -> str | None:
        if event.is_private_chat():
            result = await self._call_action(
                event,
                "get_private_file_url",
                [{"file_id": file_id}],
            )
        else:
            group_id = event.get_group_id()
            if not group_id:
                return None
            result = await self._call_action(
                event,
                "get_group_file_url",
                [{"file_id": file_id, "group_id": group_id}],
            )
        if not isinstance(result, Mapping):
            return None
        candidate = result.get("url")
        return (
            candidate
            if isinstance(candidate, str)
            and candidate.startswith(("http://", "https://"))
            else None
        )

    async def _get_forward_msg(
        self, event: Any, forward_id: str
    ) -> Mapping[str, Any] | None:
        params: list[dict[str, object]] = [
            {"message_id": forward_id},
            {"id": forward_id},
        ]
        if forward_id.isdigit():
            params.extend(
                [
                    {"message_id": int(forward_id)},
                    {"id": int(forward_id)},
                ]
            )
        result = await self._call_action(event, "get_forward_msg", params)
        return result if isinstance(result, Mapping) else None

    async def _call_action(
        self,
        event: Any,
        action: str,
        params_list: list[dict[str, object]],
    ) -> Mapping[str, Any] | None:
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            return None

        self_id = event.get_self_id()
        for params in params_list:
            routed = dict(params)
            if self_id:
                routed.setdefault(
                    "self_id", int(self_id) if str(self_id).isdigit() else self_id
                )
            for numeric_key in ("group_id", "user_id"):
                numeric_value = routed.get(numeric_key)
                if isinstance(numeric_value, str) and numeric_value.isdigit():
                    routed[numeric_key] = int(numeric_value)
            try:
                result = call_action(action, **routed)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                logger.debug("OneBot action %s 调用失败: %s", action, exc)
                continue
            if not isinstance(result, Mapping):
                continue
            data = result.get("data")
            if isinstance(data, Mapping):
                return data
            return result
        return None

    @staticmethod
    def _raw_get(raw: object, key: str) -> Any:
        getter = getattr(raw, "get", None)
        if callable(getter):
            return getter(key)
        return None
