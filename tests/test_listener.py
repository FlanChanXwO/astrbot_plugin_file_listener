from __future__ import annotations

import asyncio

import pytest
from astrbot_plugin_file_listener.core.listener import FileListener, FilterChain
from astrbot_plugin_file_listener.core.models import (
    CallbackBinding,
    FileEvent,
    FileEventBatch,
    FilterContext,
    FilterSpec,
    ListenerOptions,
)


def make_batch(*names: str) -> FileEventBatch:
    files = tuple(
        FileEvent(
            file_name=name,
            file_url=f"https://example.invalid/{name}",
            file_id=f"id-{index}",
            file_size=100 + index,
            platform="telegram",
            chat_type="group",
            source_type="message",
            chat_id="chat-1",
            sender_id="user-1",
        )
        for index, name in enumerate(names)
    )
    return FileEventBatch(
        files=files,
        raw_event=object(),
        platform="telegram",
        source_type="message",
        event_id="event-1",
        platform_id="telegram:primary",
    )


def test_file_event_batch_preserves_files_as_immutable_tuple() -> None:
    file_event = FileEvent(
        file_name="demo.zip",
        file_url="https://example.invalid/demo.zip",
        file_id="file-1",
        file_size=128,
        platform="telegram",
        chat_type="group",
        source_type="message",
        chat_id="chat-1",
        sender_id="user-1",
    )

    batch = FileEventBatch(
        files=(file_event,),
        raw_event=object(),
        platform="telegram",
        source_type="message",
        event_id="event-1",
    )

    assert batch.files == (file_event,)
    assert isinstance(batch.files, tuple)


def test_filter_context_keeps_original_batch_and_private_working_files() -> None:
    file_event = FileEvent(
        file_name="demo.zip",
        file_url=None,
        file_id=None,
        file_size=None,
        platform="aiocqhttp",
        chat_type="private",
        source_type="private_file",
        chat_id="user-1",
        sender_id="user-1",
    )
    batch = FileEventBatch(
        files=(file_event,),
        raw_event=object(),
        platform="aiocqhttp",
        source_type="private_file",
    )

    context = FilterContext.from_batch(batch)
    context.current_files.clear()

    assert context.original_batch.files == (file_event,)
    assert context.current_files == []

    with pytest.raises(AttributeError):
        context.original_batch = make_batch("replacement.zip")


def test_listener_options_default_to_parallel_execution() -> None:
    assert ListenerOptions().parallel is True


def test_callback_binding_keeps_one_callback_and_optional_filter_chain() -> None:
    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options

    marker = object()
    binding = CallbackBinding(callback=callback, filter_chain=marker)

    assert binding.callback is callback
    assert binding.filter_chain is marker


@pytest.mark.asyncio
async def test_dispatch_invokes_each_callback_once_for_multi_file_batch() -> None:
    calls: list[tuple[str, ...]] = []

    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        del options
        calls.append(tuple(file.file_name for file in batch.files))

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register([CallbackBinding(callback=callback)])

    await listener.dispatch(make_batch("a.zip", "b.zip"))

    assert calls == [("a.zip", "b.zip")]


@pytest.mark.asyncio
async def test_filter_chain_uses_stable_priority_order_and_same_context() -> None:
    seen: list[tuple[str, int]] = []

    async def later(context: FilterContext) -> FilterContext:
        seen.append(("later", id(context)))
        return context

    async def first(context: FilterContext) -> FilterContext:
        seen.append(("first", id(context)))
        context.current_files[:] = context.current_files[:1]
        return context

    async def same_priority(context: FilterContext) -> FilterContext:
        seen.append(("same", id(context)))
        return context

    received: list[tuple[str, ...]] = []

    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        del options
        received.append(tuple(file.file_name for file in batch.files))

    chain = FilterChain(
        [
            FilterSpec(callback=later, priority=200),
            FilterSpec(callback=first, priority=100),
            FilterSpec(callback=same_priority, priority=100),
        ]
    )
    listener = FileListener(ListenerOptions(parallel=False))
    listener.register([CallbackBinding(callback=callback, filter_chain=chain)])

    await listener.dispatch(make_batch("a.zip", "b.zip"))

    assert [name for name, _ in seen] == ["first", "same", "later"]
    assert len({context_id for _, context_id in seen}) == 1
    assert received == [("a.zip",)]


@pytest.mark.asyncio
async def test_empty_filter_result_skips_only_its_callback() -> None:
    calls: list[str] = []

    async def remove_all(context: FilterContext) -> FilterContext:
        context.current_files.clear()
        return context

    async def skipped(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("skipped")

    async def kept(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("kept")

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register(
        [
            CallbackBinding(
                callback=skipped,
                filter_chain=FilterChain([FilterSpec(remove_all)]),
            ),
            CallbackBinding(callback=kept),
        ]
    )

    await listener.dispatch(make_batch("a.zip"))

    assert calls == ["kept"]


@pytest.mark.asyncio
async def test_filter_failure_is_isolated_to_its_binding() -> None:
    calls: list[str] = []

    async def broken_filter(context: FilterContext) -> FilterContext:
        del context
        raise RuntimeError("boom")

    async def blocked(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("blocked")

    async def healthy(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("healthy")

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register(
        [
            CallbackBinding(
                callback=blocked,
                filter_chain=FilterChain([FilterSpec(broken_filter)]),
            ),
            CallbackBinding(callback=healthy),
        ]
    )

    await listener.dispatch(make_batch("a.zip"))

    assert calls == ["healthy"]


@pytest.mark.asyncio
async def test_callback_failure_does_not_block_later_binding() -> None:
    calls: list[str] = []

    async def broken(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("broken")
        raise RuntimeError("boom")

    async def healthy(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("healthy")

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register(
        [CallbackBinding(callback=broken), CallbackBinding(callback=healthy)]
    )

    await listener.dispatch(make_batch("a.zip"))

    assert calls == ["broken", "healthy"]


@pytest.mark.asyncio
async def test_parallel_mode_runs_complete_bindings_concurrently() -> None:
    both_started = asyncio.Event()
    started = 0
    lock = asyncio.Lock()

    def make_callback():
        async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
            nonlocal started
            del batch, options
            async with lock:
                started += 1
                if started == 2:
                    both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        return callback

    listener = FileListener(ListenerOptions(parallel=True))
    listener.register(
        [
            CallbackBinding(callback=make_callback()),
            CallbackBinding(callback=make_callback()),
        ]
    )

    await listener.dispatch(make_batch("a.zip"))

    assert started == 2


def test_register_rejects_duplicate_callback_atomically() -> None:
    async def first(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options

    async def second(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options

    listener = FileListener(ListenerOptions())
    listener.register([CallbackBinding(callback=first)])

    with pytest.raises(ValueError, match="重复注册"):
        listener.register(
            [CallbackBinding(callback=second), CallbackBinding(callback=first)]
        )

    assert listener.register([CallbackBinding(callback=second)]).active is True


def test_register_treats_repeated_bound_method_as_same_callback() -> None:
    class Consumer:
        async def on_file(
            self, batch: FileEventBatch, options: ListenerOptions
        ) -> None:
            del batch, options

    consumer = Consumer()
    listener = FileListener(ListenerOptions())
    listener.register([CallbackBinding(callback=consumer.on_file)])

    with pytest.raises(ValueError, match="重复注册"):
        listener.register([CallbackBinding(callback=consumer.on_file)])


@pytest.mark.asyncio
async def test_register_accepts_reload_compatible_structural_binding() -> None:
    class PreviousGenerationChain:
        async def run(self, context: FilterContext) -> FilterContext:
            context.current_files[:] = context.current_files[:1]
            return context

    class PreviousGenerationBinding:
        def __init__(self, callback) -> None:
            self.callback = callback
            self.filter_chain = PreviousGenerationChain()

    received: list[tuple[str, ...]] = []

    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        del options
        received.append(tuple(file.file_name for file in batch.files))

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register([PreviousGenerationBinding(callback)])

    await listener.dispatch(make_batch("a.zip", "b.zip"))

    assert received == [("a.zip",)]


@pytest.mark.asyncio
async def test_filter_returning_replacement_context_aborts_only_its_binding() -> None:
    calls: list[str] = []

    async def invalid(context: FilterContext) -> FilterContext:
        return FilterContext.from_batch(context.original_batch)

    async def blocked(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("blocked")

    async def healthy(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        calls.append("healthy")

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register(
        [
            CallbackBinding(
                callback=blocked,
                filter_chain=FilterChain([FilterSpec(invalid)]),
            ),
            CallbackBinding(callback=healthy),
        ]
    )

    await listener.dispatch(make_batch("a.zip"))

    assert calls == ["healthy"]


@pytest.mark.asyncio
async def test_continue_chain_false_stops_filters_but_still_runs_callback() -> None:
    order: list[str] = []

    async def stop(context: FilterContext) -> FilterContext:
        order.append("stop")
        context.continue_chain = False
        return context

    async def unreachable(context: FilterContext) -> FilterContext:
        order.append("unreachable")
        return context

    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        del batch, options
        order.append("callback")

    listener = FileListener(ListenerOptions(parallel=False))
    listener.register(
        [
            CallbackBinding(
                callback=callback,
                filter_chain=FilterChain(
                    [FilterSpec(stop, priority=10), FilterSpec(unreachable, priority=20)]
                ),
            )
        ]
    )

    await listener.dispatch(make_batch("a.zip"))

    assert order == ["stop", "callback"]


@pytest.mark.asyncio
async def test_registration_handle_unregister_is_idempotent() -> None:
    calls = 0

    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        nonlocal calls
        del batch, options
        calls += 1

    listener = FileListener(ListenerOptions())
    handle = listener.register([CallbackBinding(callback=callback)])
    handle.unregister()
    handle.unregister()

    await listener.dispatch(make_batch("a.zip"))

    assert calls == 0
    assert handle.active is False


@pytest.mark.asyncio
async def test_close_clears_registry_and_invalidates_handle() -> None:
    calls = 0

    async def callback(batch: FileEventBatch, options: ListenerOptions) -> None:
        nonlocal calls
        del batch, options
        calls += 1

    listener = FileListener(ListenerOptions())
    handle = listener.register([CallbackBinding(callback=callback)])
    listener.close()

    await listener.dispatch(make_batch("a.zip"))

    assert calls == 0
    assert handle.active is False
    with pytest.raises(RuntimeError, match="已关闭"):
        listener.register([CallbackBinding(callback=callback)])
