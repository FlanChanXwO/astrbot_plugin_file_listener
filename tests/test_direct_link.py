from __future__ import annotations

import pytest
from astrbot_plugin_file_listener.core.direct_link import (
    DEFAULT_DIRECT_LINK_TEMPLATE,
    SendDirectLinkFilter,
    create_direct_link_binding,
    render_file_template,
    validate_direct_link_template,
)
from astrbot_plugin_file_listener.core.formatting import format_file_size
from astrbot_plugin_file_listener.core.link_validation import LinkValidationResult
from astrbot_plugin_file_listener.core.listener import FileListener
from astrbot_plugin_file_listener.core.models import (
    FileEvent,
    FileEventBatch,
    FilterContext,
    FilterSpec,
    ListenerOptions,
)

from astrbot.api.message_components import Plain


class FakeSendEvent:
    def __init__(self, order: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.order = order

    async def send(self, chain) -> None:
        text = "".join(
            component.text
            for component in chain.chain
            if isinstance(component, Plain)
        )
        self.sent.append(text)
        if self.order is not None:
            self.order.append("send")


def make_file(
    name: str,
    *,
    url: str | None = None,
    size: int | None = 100,
) -> FileEvent:
    return FileEvent(
        file_name=name,
        file_url=url,
        file_id=f"id-{name}",
        file_size=size,
        platform="telegram",
        chat_type="group",
        source_type="message",
        chat_id="10001",
        sender_id="20001",
    )


def make_batch(event: FakeSendEvent, *files: FileEvent) -> FileEventBatch:
    return FileEventBatch(
        files=tuple(files),
        raw_event=event,
        platform="telegram",
        source_type="message",
        platform_id="telegram:primary",
    )


def test_default_template_renders_name_size_and_url() -> None:
    rendered = render_file_template(
        DEFAULT_DIRECT_LINK_TEMPLATE,
        make_file("a.zip", url="https://example.invalid/a.zip", size=321),
    )

    assert rendered == "文件名：a.zip\n大小：321 B\n链接：https://example.invalid/a.zip"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1 MB"),
        (int(2.5 * 1024**3), "2.5 GB"),
    ],
)
def test_file_size_formatter_uses_human_readable_units(size: int, expected: str) -> None:
    assert format_file_size(size) == expected


def test_missing_size_removes_entire_size_line() -> None:
    rendered = render_file_template(
        DEFAULT_DIRECT_LINK_TEMPLATE,
        make_file("a.zip", url="https://example.invalid/a.zip", size=None),
    )

    assert rendered == "文件名：a.zip\n链接：https://example.invalid/a.zip"


def test_unknown_template_placeholder_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知模板变量"):
        validate_direct_link_template("{file_name}\n{unknown}")


@pytest.mark.parametrize(
    "template",
    [
        "文件名：{file_name}",
        "链接：{file_url}",
        "大小：{file_size}",
    ],
)
def test_template_requires_file_name_and_file_url(template: str) -> None:
    with pytest.raises(ValueError, match="必须包含"):
        validate_direct_link_template(template)


@pytest.mark.asyncio
async def test_send_filter_returns_same_context_and_skips_missing_urls() -> None:
    event = FakeSendEvent()
    batch = make_batch(
        event,
        make_file("a.zip", url=None),
        make_file("b.zip", url="https://example.invalid/b.zip"),
    )
    context = FilterContext.from_batch(batch)
    filter_ = SendDirectLinkFilter(ListenerOptions(file_reply_mode="aggregate"))

    returned = await filter_(context)

    assert returned is context
    assert event.sent == [
        "文件名：b.zip\n大小：100 B\n链接：https://example.invalid/b.zip"
    ]


@pytest.mark.asyncio
async def test_send_filter_does_not_expose_local_paths() -> None:
    event = FakeSendEvent()
    batch = make_batch(event, make_file("a.zip", url="/srv/private/a.zip"))

    await SendDirectLinkFilter(ListenerOptions())(FilterContext.from_batch(batch))

    assert event.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply_mode", "expected_count"),
    [("smart", 1), ("aggregate", 1), ("separate", 2)],
)
async def test_reply_modes_control_message_count(
    reply_mode: str, expected_count: int
) -> None:
    event = FakeSendEvent()
    batch = make_batch(
        event,
        make_file("a.zip", url="https://example.invalid/a.zip"),
        make_file("b.zip", url="https://example.invalid/b.zip"),
    )
    options = ListenerOptions(file_reply_mode=reply_mode)

    await SendDirectLinkFilter(options)(FilterContext.from_batch(batch))

    assert len(event.sent) == expected_count
    if expected_count == 1:
        assert "a.zip" in event.sent[0]
        assert "b.zip" in event.sent[0]


@pytest.mark.asyncio
async def test_direct_link_disabled_sends_nothing() -> None:
    event = FakeSendEvent()
    batch = make_batch(
        event, make_file("a.zip", url="https://example.invalid/a.zip")
    )
    options = ListenerOptions(send_direct_link=False)

    await SendDirectLinkFilter(options)(FilterContext.from_batch(batch))

    assert event.sent == []


@pytest.mark.asyncio
async def test_direct_link_binding_sends_once_and_terminal_callback_is_noop() -> None:
    event = FakeSendEvent()
    options = ListenerOptions(parallel=False)
    listener = FileListener(options)
    listener.register([create_direct_link_binding(options)])

    await listener.dispatch(
        make_batch(event, make_file("a.zip", url="https://example.invalid/a.zip"))
    )

    assert len(event.sent) == 1


@pytest.mark.asyncio
async def test_direct_link_binding_filters_confirmed_invalid_file_urls_before_send() -> None:
    event = FakeSendEvent()
    options = ListenerOptions(parallel=False)

    async def probe(url: str) -> LinkValidationResult:
        del url
        return LinkValidationResult.INVALID

    listener = FileListener(options)
    listener.register([create_direct_link_binding(options, link_probe=probe)])

    await listener.dispatch(
        make_batch(event, make_file("expired.pdf", url="https://example.invalid/file"))
    )

    assert event.sent == []


@pytest.mark.asyncio
async def test_direct_link_binding_fails_open_when_link_validator_cannot_check() -> None:
    event = FakeSendEvent()
    options = ListenerOptions(parallel=False)

    async def probe(url: str) -> LinkValidationResult:
        del url
        raise TimeoutError("validator timed out")

    listener = FileListener(options)
    listener.register([create_direct_link_binding(options, link_probe=probe)])

    await listener.dispatch(
        make_batch(event, make_file("slow.pdf", url="https://example.invalid/file"))
    )

    assert len(event.sent) == 1
    assert "slow.pdf" in event.sent[0]


@pytest.mark.asyncio
async def test_direct_link_binding_does_not_probe_when_direct_link_is_disabled() -> None:
    event = FakeSendEvent()
    options = ListenerOptions(parallel=False, send_direct_link=False)
    probe_calls = 0

    async def probe(url: str) -> LinkValidationResult:
        nonlocal probe_calls
        del url
        probe_calls += 1
        return LinkValidationResult.VALID

    listener = FileListener(options)
    listener.register([create_direct_link_binding(options, link_probe=probe)])

    await listener.dispatch(
        make_batch(event, make_file("disabled.pdf", url="https://example.invalid/file"))
    )

    assert probe_calls == 0
    assert event.sent == []


@pytest.mark.asyncio
async def test_system_send_filter_runs_before_any_priority_filter() -> None:
    order: list[str] = []
    event = FakeSendEvent(order)
    options = ListenerOptions(parallel=False)

    async def earlier_priority(context: FilterContext) -> FilterContext:
        order.append("custom")
        return context

    binding = create_direct_link_binding(
        options,
        filters=[FilterSpec(callback=earlier_priority, priority=-10**12)],
    )
    listener = FileListener(options)
    listener.register([binding])

    await listener.dispatch(
        make_batch(event, make_file("a.zip", url="https://example.invalid/a.zip"))
    )

    assert order == ["send", "custom"]
