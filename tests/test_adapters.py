from __future__ import annotations

from types import SimpleNamespace

import pytest
from astrbot_plugin_file_listener.core.adapters.onebot import OneBotFileAdapter
from astrbot_plugin_file_listener.core.adapters.telegram import TelegramFileAdapter
from astrbot_plugin_file_listener.core.models import ListenerOptions

from astrbot.api.message_components import File, Forward, Plain


class FakeEvent:
    def __init__(
        self,
        *,
        platform: str,
        messages: list[object] | None = None,
        raw_message: object | None = None,
        private: bool = False,
        group_id: str = "10001",
        session_id: str = "10001",
        sender_id: str = "20001",
        message_id: str = "msg-1",
        bot: object | None = None,
    ) -> None:
        self._platform = platform
        self._messages = messages or []
        self._private = private
        self._group_id = "" if private else group_id
        self._session_id = session_id
        self._sender_id = sender_id
        self.message_obj = SimpleNamespace(
            raw_message=raw_message,
            message_id=message_id,
        )
        self.bot = bot

    def get_messages(self) -> list[object]:
        return self._messages

    def get_platform_name(self) -> str:
        return self._platform

    def get_platform_id(self) -> str:
        return f"{self._platform}:primary"

    def is_private_chat(self) -> bool:
        return self._private

    def get_group_id(self) -> str:
        return self._group_id

    def get_session_id(self) -> str:
        return self._session_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return "30001"


class FakeActionApi:
    def __init__(self, responses: dict[tuple[str, str], object] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_action(self, action: str, **params):
        self.calls.append((action, params))
        lookup = str(params.get("id") or params.get("message_id") or params.get("file_id"))
        response = self.responses.get((action, lookup))
        if isinstance(response, Exception):
            raise response
        return response


class FakeBot:
    def __init__(self, api: FakeActionApi):
        self.api = api


@pytest.mark.asyncio
async def test_telegram_document_uses_public_file_component_and_raw_metadata() -> None:
    document = SimpleNamespace(file_id="tg-file-1", file_size=321, file_name="a.zip")
    raw = SimpleNamespace(
        message=SimpleNamespace(document=document, forward_origin=None)
    )
    event = FakeEvent(
        platform="telegram",
        messages=[File(name="a.zip", url="https://t.me/file/a.zip")],
        raw_message=raw,
    )
    adapter = TelegramFileAdapter()

    source = adapter.classify(event)
    batch = await adapter.extract(event, source, ListenerOptions())

    assert source == "message"
    assert batch is not None
    assert batch.platform == "telegram"
    assert batch.platform_id == "telegram:primary"
    assert batch.files[0].file_id == "tg-file-1"
    assert batch.files[0].file_size == 321
    assert batch.files[0].file_url == "https://t.me/file/a.zip"


@pytest.mark.asyncio
async def test_telegram_forwarded_document_has_distinct_source() -> None:
    raw = SimpleNamespace(
        message=SimpleNamespace(
            document=SimpleNamespace(file_id="tg-file-1", file_size=10),
            forward_origin=object(),
        )
    )
    event = FakeEvent(
        platform="telegram",
        messages=[File(name="a.zip", url="https://t.me/file/a.zip")],
        raw_message=raw,
    )
    adapter = TelegramFileAdapter()

    source = adapter.classify(event)
    batch = await adapter.extract(event, source, ListenerOptions())

    assert source == "forwarded"
    assert batch is not None
    assert batch.source_type == "forwarded"


@pytest.mark.asyncio
async def test_telegram_non_file_event_is_ignored() -> None:
    event = FakeEvent(platform="telegram", messages=[Plain("hello")])
    adapter = TelegramFileAdapter()

    assert adapter.classify(event) is None
    assert await adapter.extract(event, None, ListenerOptions()) is None


@pytest.mark.asyncio
async def test_telegram_local_component_path_is_not_exposed_as_direct_url() -> None:
    raw = SimpleNamespace(
        message=SimpleNamespace(
            document=SimpleNamespace(file_id="tg-file-1", file_size=10),
            forward_origin=None,
        )
    )
    event = FakeEvent(
        platform="telegram",
        messages=[File(name="a.zip", url="/srv/private/a.zip")],
        raw_message=raw,
    )
    adapter = TelegramFileAdapter()

    batch = await adapter.extract(event, adapter.classify(event), ListenerOptions())

    assert batch is not None
    assert batch.files[0].file_url is None


@pytest.mark.asyncio
async def test_onebot_group_and_private_file_sources_are_mutually_exclusive() -> None:
    raw = {
        "post_type": "message",
        "message_id": 123,
        "message": [
            {
                "type": "file",
                "data": {"file_id": "ob-file-1", "file_size": 456},
            }
        ],
    }
    group_event = FakeEvent(
        platform="aiocqhttp",
        messages=[File(name="a.zip", url="https://qq/file/a.zip")],
        raw_message=raw,
    )
    private_event = FakeEvent(
        platform="aiocqhttp",
        messages=[File(name="a.zip", url="https://qq/file/a.zip")],
        raw_message=raw,
        private=True,
        session_id="20001",
    )
    adapter = OneBotFileAdapter()

    assert adapter.classify(group_event) == "message"
    assert adapter.classify(private_event) == "private_file"

    batch = await adapter.extract(group_event, "message", ListenerOptions())
    assert batch is not None
    assert batch.files[0].file_id == "ob-file-1"
    assert batch.files[0].file_size == 456


@pytest.mark.asyncio
async def test_onebot_preserves_zero_byte_file_size() -> None:
    raw = {
        "post_type": "message",
        "message_id": 124,
        "message": [
            {
                "type": "file",
                "data": {"file_id": "empty-file", "file_size": 0},
            }
        ],
    }
    event = FakeEvent(
        platform="aiocqhttp",
        messages=[File(name="empty.bin", url="https://qq/file/empty.bin")],
        raw_message=raw,
    )
    adapter = OneBotFileAdapter()

    batch = await adapter.extract(event, "message", ListenerOptions())

    assert batch is not None
    assert batch.files[0].file_size == 0


@pytest.mark.asyncio
async def test_onebot_local_component_path_is_not_exposed_as_direct_url() -> None:
    event = FakeEvent(
        platform="aiocqhttp",
        messages=[File(name="a.zip", url="/srv/private/a.zip")],
        raw_message={
            "post_type": "message",
            "message_id": 123,
            "message": [{"type": "file", "data": {"file_id": "ob-file-1"}}],
        },
    )
    adapter = OneBotFileAdapter()

    batch = await adapter.extract(event, adapter.classify(event), ListenerOptions())

    assert batch is not None
    assert batch.files[0].file_url is None


@pytest.mark.asyncio
async def test_onebot_group_upload_notice_can_fill_url_via_action() -> None:
    api = FakeActionApi(
        {("get_group_file_url", "ob-file-1"): {"data": {"url": "https://qq/file/a.zip"}}}
    )
    raw = {
        "post_type": "notice",
        "notice_type": "group_upload",
        "group_id": 10001,
        "user_id": 20001,
        "file": {"id": "ob-file-1", "name": "a.zip", "size": 456},
    }
    event = FakeEvent(
        platform="aiocqhttp",
        raw_message=raw,
        bot=FakeBot(api),
    )
    adapter = OneBotFileAdapter()

    source = adapter.classify(event)
    batch = await adapter.extract(event, source, ListenerOptions())

    assert source == "group_upload_notice"
    assert batch is not None
    assert batch.files[0].file_name == "a.zip"
    assert batch.files[0].file_id == "ob-file-1"
    assert batch.files[0].file_size == 456
    assert batch.files[0].file_url == "https://qq/file/a.zip"
    assert api.calls[0][0] == "get_group_file_url"
    assert api.calls[0][1]["group_id"] == 10001
    assert api.calls[0][1]["self_id"] == 30001


@pytest.mark.asyncio
async def test_onebot_upload_notice_keeps_event_when_url_action_fails() -> None:
    api = FakeActionApi({("get_group_file_url", "ob-file-1"): RuntimeError("no url")})
    event = FakeEvent(
        platform="aiocqhttp",
        raw_message={
            "post_type": "notice",
            "notice_type": "group_upload",
            "group_id": 10001,
            "user_id": 20001,
            "file": {"id": "ob-file-1", "name": "a.zip", "size": 456},
        },
        bot=FakeBot(api),
    )
    adapter = OneBotFileAdapter()

    batch = await adapter.extract(
        event, adapter.classify(event), ListenerOptions()
    )

    assert batch is not None
    assert batch.files[0].file_url is None


@pytest.mark.asyncio
async def test_onebot_merged_forward_returns_one_batch_and_honors_depth() -> None:
    api = FakeActionApi(
        {
            (
                "get_forward_msg",
                "fwd-1",
            ): {
                "data": {
                    "messages": [
                        {
                            "message": [
                                {
                                    "type": "file",
                                    "data": {
                                        "file_id": "ob-file-1",
                                        "file_name": "a.zip",
                                        "file_size": 100,
                                        "url": "https://qq/file/a.zip",
                                    },
                                },
                                {"type": "forward", "data": {"id": "fwd-2"}},
                            ]
                        }
                    ]
                }
            },
            (
                "get_forward_msg",
                "fwd-2",
            ): {
                "data": {
                    "messages": [
                        {
                            "message": [
                                {
                                    "type": "file",
                                    "data": {
                                        "file_id": "ob-file-2",
                                        "file_name": "b.zip",
                                        "file_size": 200,
                                        "url": "https://qq/file/b.zip",
                                    },
                                }
                            ]
                        }
                    ]
                }
            },
        }
    )
    event = FakeEvent(
        platform="aiocqhttp",
        messages=[Forward(id="fwd-1")],
        raw_message={"post_type": "message", "message_id": 99},
        bot=FakeBot(api),
    )
    adapter = OneBotFileAdapter()

    batch = await adapter.extract(
        event,
        adapter.classify(event),
        ListenerOptions(forward_max_depth=2),
    )

    assert batch is not None
    assert batch.source_type == "merged_forward"
    assert [file.file_name for file in batch.files] == ["a.zip", "b.zip"]
    assert [call[0] for call in api.calls] == ["get_forward_msg", "get_forward_msg"]

    api.calls.clear()
    shallow = await adapter.extract(
        event,
        "merged_forward",
        ListenerOptions(forward_max_depth=1),
    )
    assert shallow is not None
    assert [file.file_name for file in shallow.files] == ["a.zip"]
    assert [call[0] for call in api.calls] == ["get_forward_msg"]


@pytest.mark.asyncio
async def test_onebot_mixed_file_and_forward_segments_keep_all_files() -> None:
    api = FakeActionApi(
        {
            (
                "get_forward_msg",
                "fwd-1",
            ): {
                "messages": [
                    {
                        "message": [
                            {
                                "type": "file",
                                "data": {
                                    "file_id": "nested-file",
                                    "file_name": "nested.zip",
                                    "url": "https://qq/file/nested.zip",
                                },
                            }
                        ]
                    }
                ]
            }
        }
    )
    event = FakeEvent(
        platform="aiocqhttp",
        messages=[
            File(name="direct.zip", url="https://qq/file/direct.zip"),
            Forward(id="fwd-1"),
        ],
        raw_message={
            "post_type": "message",
            "message_id": 99,
            "message": [
                {
                    "type": "file",
                    "data": {"file_id": "direct-file", "file_size": 10},
                },
                {"type": "forward", "data": {"id": "fwd-1"}},
            ],
        },
        bot=FakeBot(api),
    )
    adapter = OneBotFileAdapter()

    batch = await adapter.extract(
        event, adapter.classify(event), ListenerOptions(forward_max_depth=2)
    )

    assert batch is not None
    assert [file.file_name for file in batch.files] == ["direct.zip", "nested.zip"]


@pytest.mark.asyncio
async def test_onebot_forward_partial_failure_keeps_files_already_extracted() -> None:
    api = FakeActionApi(
        {
            (
                "get_forward_msg",
                "fwd-1",
            ): {
                "messages": [
                    {
                        "content": [
                            {
                                "type": "file",
                                "data": {
                                    "file_id": "ob-file-1",
                                    "file_name": "a.zip",
                                    "url": "https://qq/file/a.zip",
                                },
                            },
                            {"type": "forward", "data": {"id": "fwd-2"}},
                        ]
                    }
                ]
            },
            ("get_forward_msg", "fwd-2"): RuntimeError("broken nested forward"),
        }
    )
    event = FakeEvent(
        platform="aiocqhttp",
        messages=[Forward(id="fwd-1")],
        bot=FakeBot(api),
    )
    adapter = OneBotFileAdapter()

    batch = await adapter.extract(
        event,
        "merged_forward",
        ListenerOptions(forward_max_depth=3),
    )

    assert batch is not None
    assert [file.file_name for file in batch.files] == ["a.zip"]
