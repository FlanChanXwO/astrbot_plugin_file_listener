from __future__ import annotations

from astrbot_plugin_file_listener.core.dedupe import Deduplicator
from astrbot_plugin_file_listener.core.models import FileEvent, FileEventBatch


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_batch(
    *,
    source: str = "message",
    event_id: str | None = None,
    file_id: str | None = "file-1",
    file_url: str | None = "https://example.invalid/a.zip",
    file_name: str = "a.zip",
    file_size: int | None = 100,
    platform: str = "aiocqhttp",
    platform_id: str = "aiocqhttp:primary",
    chat_id: str = "10001",
) -> FileEventBatch:
    file_event = FileEvent(
        file_name=file_name,
        file_url=file_url,
        file_id=file_id,
        file_size=file_size,
        platform=platform,
        chat_type="group",
        source_type=source,
        chat_id=chat_id,
        sender_id="20001",
    )
    return FileEventBatch(
        files=(file_event,),
        raw_event=object(),
        platform=platform,
        source_type=source,
        event_id=event_id,
        platform_id=platform_id,
    )


def test_same_trusted_event_id_is_duplicate_within_window() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    assert dedupe.is_duplicate(make_batch(event_id="event-1")) is False
    assert dedupe.is_duplicate(make_batch(event_id="event-1")) is True


def test_same_event_id_in_different_chats_is_not_duplicate() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    assert dedupe.is_duplicate(make_batch(event_id="42", chat_id="chat-a")) is False
    assert dedupe.is_duplicate(make_batch(event_id="42", chat_id="chat-b")) is False


def test_onebot_message_and_upload_notice_share_mirror_family() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    assert dedupe.is_duplicate(make_batch(source="message")) is False
    assert dedupe.is_duplicate(make_batch(source="group_upload_notice")) is True


def test_onebot_mirror_events_dedupe_when_platform_file_ids_differ() -> None:
    """NapCat 的 group_upload notice 与普通 file message 会使用不同 file_id。"""
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    message = make_batch(
        source="message",
        file_id="raw-file-uuid",
        file_name="TotalWarWarhammerIIITrainer.zip",
        file_size=123456,
    )
    notice = make_batch(
        source="group_upload_notice",
        file_id="napcat-encoded-notice-id",
        file_name="TotalWarWarhammerIIITrainer.zip",
        file_size=123456,
    )

    assert dedupe.is_duplicate(message) is False
    assert dedupe.is_duplicate(notice) is True


def test_forward_is_not_deduped_against_normal_upload() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    assert dedupe.is_duplicate(make_batch(source="message")) is False
    assert dedupe.is_duplicate(make_batch(source="merged_forward")) is False


def test_identity_falls_back_from_file_id_to_url_then_name_and_size() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    first = make_batch(source="message", file_id=None)
    mirror = make_batch(source="group_upload_notice", file_id=None)
    assert dedupe.is_duplicate(first) is False
    assert dedupe.is_duplicate(mirror) is True

    dedupe.clear()
    first = make_batch(source="message", file_id=None, file_url=None)
    mirror = make_batch(source="group_upload_notice", file_id=None, file_url=None)
    assert dedupe.is_duplicate(first) is False
    assert dedupe.is_duplicate(mirror) is True


def test_weak_identity_does_not_suppress_same_source_reupload() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)
    first = make_batch(source="message", file_id=None, file_url=None)
    second = make_batch(source="message", file_id=None, file_url=None)

    assert dedupe.is_duplicate(first) is False
    assert dedupe.is_duplicate(second) is False


def test_multi_file_fingerprint_is_order_independent() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)
    a = make_batch(source="message").files[0]
    b = FileEvent(
        file_name="b.zip",
        file_url="https://example.invalid/b.zip",
        file_id="file-2",
        file_size=200,
        platform="aiocqhttp",
        chat_type="group",
        source_type="message",
        chat_id="10001",
        sender_id="20001",
    )
    first = FileEventBatch(
        files=(a, b),
        raw_event=object(),
        platform="aiocqhttp",
        source_type="message",
        platform_id="aiocqhttp:primary",
    )
    mirror = FileEventBatch(
        files=(b, a),
        raw_event=object(),
        platform="aiocqhttp",
        source_type="group_upload_notice",
        platform_id="aiocqhttp:primary",
    )

    assert dedupe.is_duplicate(first) is False
    assert dedupe.is_duplicate(mirror) is True


def test_window_expiry_allows_same_file_again() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)

    assert dedupe.is_duplicate(make_batch(source="message")) is False
    clock.advance(2.1)
    assert dedupe.is_duplicate(make_batch(source="group_upload_notice")) is False


def test_disabled_dedupe_never_suppresses_events() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=False, window_seconds=2.0, clock=clock)

    batch = make_batch(event_id="event-1")
    assert dedupe.is_duplicate(batch) is False
    assert dedupe.is_duplicate(batch) is False


def test_batch_without_reliable_identity_is_not_cross_event_deduped() -> None:
    clock = FakeClock()
    dedupe = Deduplicator(enabled=True, window_seconds=2.0, clock=clock)
    first = make_batch(
        source="message", file_id=None, file_url=None, file_size=None
    )
    mirror = make_batch(
        source="group_upload_notice", file_id=None, file_url=None, file_size=None
    )

    assert dedupe.is_duplicate(first) is False
    assert dedupe.is_duplicate(mirror) is False
