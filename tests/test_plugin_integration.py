from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_file_listener.core.config import build_listener_options
from astrbot_plugin_file_listener.core.direct_link import DEFAULT_DIRECT_LINK_TEMPLATE
from astrbot_plugin_file_listener.core.models import CallbackBinding, FileEventBatch
from astrbot_plugin_file_listener.main import FileListenerPlugin

from astrbot.api.message_components import File, Plain

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class FakeTelegramEvent:
    def __init__(
        self,
        *,
        message_id: str = "msg-1",
        files: int = 1,
        sender_id: str = "20001",
        self_id: str = "bot-1",
    ) -> None:
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            raw_message=SimpleNamespace(
                message=SimpleNamespace(
                    document=SimpleNamespace(
                        file_id="tg-file-1",
                        file_size=123,
                        file_name="a.zip",
                    ),
                    forward_origin=None,
                )
            ),
        )
        self._messages = [
            File(
                name=f"{index}.zip",
                url=f"https://example.invalid/{index}.zip",
            )
            for index in range(files)
        ]
        self.sent: list[str] = []
        self._sender_id = sender_id
        self._self_id = self_id

    def get_platform_name(self) -> str:
        return "telegram"

    def get_platform_id(self) -> str:
        return "telegram:primary"

    def get_messages(self):
        return self._messages

    def is_private_chat(self) -> bool:
        return False

    def get_group_id(self) -> str:
        return "10001"

    def get_session_id(self) -> str:
        return "10001"

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id

    async def send(self, chain) -> None:
        self.sent.append(
            "".join(
                component.text
                for component in chain.chain
                if isinstance(component, Plain)
            )
        )


def test_default_config_builds_expected_listener_options() -> None:
    options = build_listener_options({})

    assert options.parallel is True
    assert options.ignore_self_messages is True
    assert options.forward_max_depth == 3
    assert options.send_direct_link is True
    assert options.file_reply_mode == "smart"
    assert options.direct_link_template == DEFAULT_DIRECT_LINK_TEMPLATE
    assert options.monitor_sources["telegram"] == frozenset({"message", "forwarded"})
    assert options.monitor_sources["onebot"] == frozenset(
        {"message", "private_file", "group_upload_notice", "merged_forward"}
    )


def test_config_accepts_source_selection_and_runtime_behavior() -> None:
    options = build_listener_options(
        {
            "runtime": {
                "parallel": False,
                "ignore_self_messages": False,
                "forward_max_depth": 5,
            },
            "monitor_sources": {
                "telegram": ["message"],
                "onebot": ["private_file", "merged_forward"],
            },
            "direct_link": {
                "enabled": False,
                "reply_mode": "separate",
                "template": "{file_name}\n{file_url}",
            },
            "dedupe": {"enabled": False, "window_seconds": 4.5},
        }
    )

    assert options.parallel is False
    assert options.ignore_self_messages is False
    assert options.forward_max_depth == 5
    assert options.monitor_sources["telegram"] == frozenset({"message"})
    assert options.monitor_sources["onebot"] == frozenset(
        {"private_file", "merged_forward"}
    )
    assert options.send_direct_link is False
    assert options.file_reply_mode == "separate"
    assert options.direct_link_template == "{file_name}\n{file_url}"
    assert options.dedupe_enabled is False
    assert options.dedupe_window_seconds == 4.5


def test_invalid_template_and_local_values_fall_back_to_defaults() -> None:
    options = build_listener_options(
        {
            "runtime": {"forward_max_depth": 0},
            "direct_link": {
                "reply_mode": "invalid",
                "template": "{file_name}\n{unknown}",
            },
            "dedupe": {"window_seconds": -1},
        }
    )

    assert options.forward_max_depth == 3
    assert options.file_reply_mode == "smart"
    assert options.direct_link_template == DEFAULT_DIRECT_LINK_TEMPLATE
    assert options.dedupe_window_seconds > 0


def test_conf_schema_uses_editor_mode_and_all_sources_by_default() -> None:
    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    assert schema["runtime"]["items"]["ignore_self_messages"]["default"] is True
    template = schema["direct_link"]["items"]["template"]
    assert template["type"] == "string"
    assert template["editor_mode"] is True
    assert template["editor_language"] == "plaintext"
    assert schema["monitor_sources"]["items"]["telegram"]["default"] == [
        "message",
        "forwarded",
    ]
    assert schema["monitor_sources"]["items"]["onebot"]["default"] == [
        "message",
        "private_file",
        "group_upload_notice",
        "merged_forward",
    ]


def test_metadata_declares_only_supported_platforms_and_strict_minimum_version() -> None:
    metadata = (PLUGIN_ROOT / "metadata.yaml").read_text(encoding="utf-8")

    assert 'astrbot_version: ">4.26.0"' in metadata
    assert "  - telegram" in metadata
    assert "  - aiocqhttp" in metadata
    assert "discord" not in metadata


@pytest.mark.asyncio
async def test_plugin_tracer_bullet_dispatches_direct_link_and_external_callback_once() -> None:
    plugin = FileListenerPlugin(context=object(), config={})
    await plugin.initialize()
    received: list[tuple[str, ...]] = []

    async def callback(batch: FileEventBatch, options) -> None:
        del options
        received.append(tuple(file.file_name for file in batch.files))

    plugin.get_file_listener().register([CallbackBinding(callback=callback)])
    event = FakeTelegramEvent(files=2)

    await plugin.on_file_event(event)

    assert received == [("0.zip", "1.zip")]
    assert len(event.sent) == 1
    assert "0.zip" in event.sent[0]
    assert "1.zip" in event.sent[0]


@pytest.mark.asyncio
async def test_plugin_deduplicates_before_all_bindings() -> None:
    plugin = FileListenerPlugin(context=object(), config={})
    await plugin.initialize()
    calls = 0

    async def callback(batch: FileEventBatch, options) -> None:
        nonlocal calls
        del batch, options
        calls += 1

    plugin.get_file_listener().register([CallbackBinding(callback=callback)])
    event = FakeTelegramEvent(message_id="same-message")

    await plugin.on_file_event(event)
    await plugin.on_file_event(event)

    assert calls == 1
    assert len(event.sent) == 1


@pytest.mark.asyncio
async def test_plugin_direct_link_can_be_disabled_without_disabling_callbacks() -> None:
    plugin = FileListenerPlugin(
        context=object(),
        config={"direct_link": {"enabled": False}},
    )
    await plugin.initialize()
    calls = 0

    async def callback(batch: FileEventBatch, options) -> None:
        nonlocal calls
        del batch, options
        calls += 1

    plugin.get_file_listener().register([CallbackBinding(callback=callback)])
    event = FakeTelegramEvent()

    await plugin.on_file_event(event)

    assert calls == 1
    assert event.sent == []


@pytest.mark.asyncio
async def test_plugin_source_gate_skips_disabled_source_before_extraction() -> None:
    plugin = FileListenerPlugin(
        context=object(),
        config={"monitor_sources": {"telegram": [], "onebot": []}},
    )
    await plugin.initialize()
    event = FakeTelegramEvent()

    await plugin.on_file_event(event)

    assert event.sent == []


@pytest.mark.asyncio
async def test_plugin_ignores_self_messages_by_default_but_can_enable_them() -> None:
    ignored = FileListenerPlugin(context=object(), config={})
    await ignored.initialize()
    ignored_calls = 0

    async def ignored_callback(batch: FileEventBatch, options) -> None:
        nonlocal ignored_calls
        del batch, options
        ignored_calls += 1

    ignored.get_file_listener().register([CallbackBinding(callback=ignored_callback)])
    ignored_event = FakeTelegramEvent(sender_id="bot-1", self_id="bot-1")

    await ignored.on_file_event(ignored_event)

    assert ignored_calls == 0
    assert ignored_event.sent == []

    enabled = FileListenerPlugin(
        context=object(),
        config={"runtime": {"ignore_self_messages": False}},
    )
    await enabled.initialize()
    enabled_calls = 0

    async def enabled_callback(batch: FileEventBatch, options) -> None:
        nonlocal enabled_calls
        del batch, options
        enabled_calls += 1

    enabled.get_file_listener().register([CallbackBinding(callback=enabled_callback)])
    enabled_event = FakeTelegramEvent(sender_id="bot-1", self_id="bot-1")

    await enabled.on_file_event(enabled_event)

    assert enabled_calls == 1
    assert len(enabled_event.sent) == 1


@pytest.mark.asyncio
async def test_onebot_message_sent_raw_hook_is_private_to_file_listener() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.original_calls = 0
            self.actions: list[tuple[str, dict[str, object]]] = []

        async def _handle_event(self, payload):
            del payload
            self.original_calls += 1
            return "original"

        async def call_action(self, action: str, **params):
            self.actions.append((action, params))
            if action == "get_group_file_url":
                return {"url": "https://example.invalid/file/?fname="}
            return {}

    class FakeMeta:
        name = "aiocqhttp"
        id = "onebot:test"

    class FakePlatform:
        def __init__(self, client) -> None:
            self._client = client

        def meta(self):
            return FakeMeta()

        def get_client(self):
            return self._client

    class FakePlatformManager:
        def __init__(self, platform) -> None:
            self._platform = platform

        def get_insts(self):
            return [self._platform]

    client = FakeClient()
    context = SimpleNamespace(
        platform_manager=FakePlatformManager(FakePlatform(client))
    )
    original_handler = client._handle_event
    plugin = FileListenerPlugin(
        context=context,
        config={"runtime": {"ignore_self_messages": False}},
    )
    await plugin.initialize()
    calls = 0

    async def callback(batch: FileEventBatch, options) -> None:
        nonlocal calls
        del options
        calls += 1
        assert batch.files[0].file_name == "self-file.zip"
        assert batch.files[0].sender_id == "1694665803"

    plugin.get_file_listener().register([CallbackBinding(callback=callback)])
    payload = {
        "post_type": "message_sent",
        "message_sent_type": "self",
        "message_type": "group",
        "self_id": 1694665803,
        "user_id": 1694665803,
        "target_id": 123456,
        "group_id": 123456,
        "message_id": 987654,
        "message": [
            {
                "type": "file",
                "data": {
                    "file": "self-file.zip",
                    "file_id": "raw-file-id",
                    "file_size": 2048,
                },
            }
        ],
    }

    result = await client._handle_event(payload)

    assert result is None
    assert client.original_calls == 0
    assert calls == 1
    assert any(action == "send_group_msg" for action, _ in client.actions)

    await plugin.terminate()
    assert client._handle_event == original_handler


@pytest.mark.asyncio
async def test_plugin_isolates_unexpected_adapter_errors() -> None:
    class BrokenAdapter:
        def classify(self, event) -> str:
            del event
            return "message"

        async def extract(self, event, source, options):
            del event, source, options
            raise RuntimeError("broken adapter")

    plugin = FileListenerPlugin(context=object(), config={})
    await plugin.initialize()
    plugin._adapters["telegram"] = BrokenAdapter()
    event = FakeTelegramEvent()

    await plugin.on_file_event(event)

    assert event.sent == []


@pytest.mark.asyncio
async def test_plugin_terminate_invalidates_listener_registration() -> None:
    plugin = FileListenerPlugin(context=object(), config={})
    await plugin.initialize()

    async def callback(batch: FileEventBatch, options) -> None:
        del batch, options

    listener = plugin.get_file_listener()
    handle = listener.register([CallbackBinding(callback=callback)])

    await plugin.terminate()

    assert handle.active is False
    with pytest.raises(RuntimeError, match="尚未初始化"):
        plugin.get_file_listener()
