from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .listener import FilterChain

DEFAULT_MONITOR_SOURCES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "telegram": frozenset({"message", "forwarded"}),
        "onebot": frozenset(
            {"message", "private_file", "group_upload_notice", "merged_forward"}
        ),
    }
)


@dataclass(frozen=True, slots=True)
class FileEvent:
    """平台无关的单文件事件。"""

    file_name: str
    file_url: str | None
    file_id: str | None
    file_size: int | None
    platform: str
    chat_type: Literal["group", "private"]
    source_type: str
    chat_id: str | None
    sender_id: str | None
    raw_file: Any = None


@dataclass(frozen=True, slots=True)
class FileEventBatch:
    """一个源事件中提取出的全部文件。"""

    files: tuple[FileEvent, ...]
    raw_event: Any
    platform: str
    source_type: str
    event_id: str | None = None
    platform_id: str | None = None


class FilterContext:
    """单个 callback binding 的过滤工作上下文。"""

    __slots__ = ("_original_batch", "current_files", "continue_chain", "reason")

    def __init__(
        self,
        original_batch: FileEventBatch,
        current_files: list[FileEvent],
        continue_chain: bool = True,
        reason: str | None = None,
    ) -> None:
        self._original_batch = original_batch
        self.current_files = current_files
        self.continue_chain = continue_chain
        self.reason = reason

    @property
    def original_batch(self) -> FileEventBatch:
        """返回不可替换的源事件批次。"""
        return self._original_batch

    @classmethod
    def from_batch(cls, batch: FileEventBatch) -> FilterContext:
        """从只读 batch 创建独立过滤工作集。

        Args:
            batch: 当前源事件批次。

        Returns:
            持有独立 current_files 列表的过滤上下文。
        """
        return cls(original_batch=batch, current_files=list(batch.files))


@dataclass(frozen=True, slots=True)
class ListenerOptions:
    """File Listener 单次插件生命周期内的只读配置快照。"""

    parallel: bool = True
    ignore_self_messages: bool = True
    send_direct_link: bool = True
    file_reply_mode: Literal["smart", "aggregate", "separate"] = "smart"
    forward_max_depth: int = 3
    dedupe_enabled: bool = True
    dedupe_window_seconds: float = 3.0
    monitor_sources: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: DEFAULT_MONITOR_SOURCES
    )
    direct_link_template: str = (
        "文件名：{file_name}\n大小：{file_size}\n链接：{file_url}"
    )


FilterCallable = Callable[[FilterContext], Awaitable[FilterContext]]
CallbackCallable = Callable[[FileEventBatch, ListenerOptions], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """一项带稳定排序优先级的异步过滤器。"""

    callback: FilterCallable
    priority: int = 100


@dataclass(frozen=True, slots=True)
class CallbackBinding:
    """一个异步 callback 与其最多一条过滤器链。"""

    callback: CallbackCallable
    filter_chain: FilterChain | None = None
