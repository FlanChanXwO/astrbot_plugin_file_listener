from __future__ import annotations

import time
from collections.abc import Callable

from .models import FileEvent, FileEventBatch


class Deduplicator:
    """在短时间窗内抑制同一文件事件的镜像来源。"""

    def __init__(
        self,
        *,
        enabled: bool,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        if window_seconds <= 0:
            raise ValueError("dedupe window_seconds 必须大于 0")
        self.enabled = enabled
        self.window_seconds = window_seconds
        self._clock = clock
        self._event_ids: dict[tuple[str, str, str], float] = {}
        self._fingerprints: dict[
            tuple[str, tuple[tuple[str, ...], ...]], tuple[float, str, bool]
        ] = {}

    def is_duplicate(self, batch: FileEventBatch) -> bool:
        """判断 batch 是否为时间窗内已经处理过的事件。

        Args:
            batch: 已完成平台规范化的文件事件批次。

        Returns:
            若应抑制本次分发则返回 True，否则记录本次事件并返回 False。
        """
        if not self.enabled:
            return False

        now = self._clock()
        self._evict_expired(now)
        platform_instance = batch.platform_id or batch.platform

        if batch.event_id:
            chat_id = batch.files[0].chat_id if batch.files else None
            event_key = (platform_instance, chat_id or "", batch.event_id)
            if event_key in self._event_ids:
                return True
            self._event_ids[event_key] = now

        fingerprints = self._batch_fingerprints(batch, platform_instance)
        if not fingerprints:
            return False

        duplicate = False
        for family, identities, weak_identity in fingerprints:
            previous = self._fingerprints.get((family, identities))
            if previous is None:
                continue
            _, previous_source, previous_weak = previous
            if (
                weak_identity or previous_weak
            ) and previous_source == batch.source_type:
                continue
            duplicate = True
            break

        for family, identities, weak_identity in fingerprints:
            self._fingerprints[(family, identities)] = (
                now,
                batch.source_type,
                weak_identity,
            )
        return duplicate

    def clear(self) -> None:
        """清空当前插件生命周期内的去重状态。"""
        self._event_ids.clear()
        self._fingerprints.clear()

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._event_ids = {
            key: timestamp
            for key, timestamp in self._event_ids.items()
            if timestamp > cutoff
        }
        self._fingerprints = {
            key: value for key, value in self._fingerprints.items() if value[0] > cutoff
        }

    def _batch_fingerprints(
        self, batch: FileEventBatch, platform_instance: str
    ) -> list[tuple[str, tuple[tuple[str, ...], ...], bool]]:
        family = self._dedupe_family(batch)
        fingerprints: list[tuple[str, tuple[tuple[str, ...], ...], bool]] = []
        for kind, weak in (("id", False), ("url", False), ("name-size", True)):
            identities: list[tuple[str, ...]] = []
            for file_event in batch.files:
                identity = self._file_identity(
                    file_event,
                    platform_instance,
                    kind=kind,
                )
                if identity is None:
                    break
                identities.append(identity)
            else:
                fingerprints.append((family, tuple(sorted(identities)), weak))
        return fingerprints

    @staticmethod
    def _file_identity(
        file_event: FileEvent,
        platform_instance: str,
        *,
        kind: str,
    ) -> tuple[str, ...] | None:
        chat_id = file_event.chat_id or ""
        if kind == "id" and file_event.file_id:
            return (platform_instance, chat_id, "id", file_event.file_id)
        if kind == "url" and file_event.file_url:
            return (platform_instance, chat_id, "url", file_event.file_url)
        if (
            kind == "name-size"
            and file_event.file_name
            and file_event.file_size is not None
        ):
            return (
                platform_instance,
                chat_id,
                "name-size",
                file_event.file_name,
                str(file_event.file_size),
            )
        return None

    @staticmethod
    def _dedupe_family(batch: FileEventBatch) -> str:
        if batch.platform == "aiocqhttp":
            if batch.source_type in {"message", "group_upload_notice"}:
                return "onebot_upload"
            if batch.source_type == "private_file":
                return "onebot_private"
            if batch.source_type == "merged_forward":
                return "onebot_forward"
        if batch.platform == "telegram":
            if batch.source_type == "forwarded":
                return "telegram_forward"
            return "telegram_message"
        return f"{batch.platform}:{batch.source_type}"
