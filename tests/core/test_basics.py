import asyncio
import pickle

import pytest

from _pytest.capture import (
    SysCapture,
)
from helpers import (
    DummyRequest,
    DummyRequestPair,
    DummyResponse,
)
from lahja import (
    BaseEvent,
    Endpoint,
    UnexpectedResponse,
)


@pytest.mark.asyncio
async def test_request(endpoint: Endpoint) -> None:
    endpoint.subscribe(
        DummyRequestPair,
        lambda ev: endpoint.broadcast_nowait(
            # Accessing `ev.property_of_dummy_request_pair` here allows us to validate
            # mypy has the type information we think it has. We run mypy on the tests.
            DummyResponse(ev.property_of_dummy_request_pair), ev.broadcast_config()
        )
    )

    item = DummyRequestPair()
    response = await endpoint.request(item)
    # Accessing `ev.property_of_dummy_response` here allows us to validate
    # mypy has the type information we think it has. We run mypy on the tests.
    print(response.property_of_dummy_response)
    assert isinstance(response, DummyResponse)
    # Ensure the registration was cleaned up
    assert item._id not in endpoint._futures


@pytest.mark.asyncio
async def test_request_can_get_cancelled(endpoint: Endpoint) -> None:

    item = DummyRequestPair()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(endpoint.request(item), 0.01)
    await asyncio.sleep(0.01)
    # Ensure the registration was cleaned up
    assert item._id not in endpoint._futures


@pytest.mark.asyncio
async def test_response_must_match(endpoint: Endpoint) -> None:
    endpoint.subscribe(
        DummyRequestPair,
        lambda ev: endpoint.broadcast_nowait(
            # We intentionally broadcast an unexpected response. Mypy can't catch
            # this but we ensure it is caught and raised during the processing.
            DummyRequest(), ev.broadcast_config()
        )
    )

    with pytest.raises(UnexpectedResponse):
        await endpoint.request(DummyRequestPair())


@pytest.mark.asyncio
async def test_stream_with_break(endpoint: Endpoint) -> None:
    stream_counter = 0

    async def stream_response() -> None:
        async for event in endpoint.stream(DummyRequest):
            # Accessing `ev.property_of_dummy_request` here allows us to validate
            # mypy has the type information we think it has. We run mypy on the tests.
            print(event.property_of_dummy_request)
            nonlocal stream_counter
            stream_counter += 1

            if stream_counter == 2:
                break

    asyncio.ensure_future(stream_response())

    # we broadcast one more item than what we consume and test for that
    for i in range(5):
        await endpoint.broadcast(DummyRequest())

    await asyncio.sleep(0.01)
    # Ensure the registration was cleaned up
    assert len(endpoint._queues[DummyRequest]) == 0
    assert stream_counter == 2


@pytest.mark.asyncio
async def test_stream_with_num_events(endpoint: Endpoint) -> None:
    stream_counter = 0

    async def stream_response() -> None:
        nonlocal stream_counter
        async for event in endpoint.stream(DummyRequest, num_events=2):
            # Accessing `ev.property_of_dummy_request` here allows us to validate
            # mypy has the type information we think it has. We run mypy on the tests.
            print(event.property_of_dummy_request)
            stream_counter += 1

    asyncio.ensure_future(stream_response())

    # we broadcast one more item than what we consume and test for that
    for i in range(3):
        await endpoint.broadcast(DummyRequest())

    await asyncio.sleep(0.01)
    # Ensure the registration was cleaned up
    assert len(endpoint._queues[DummyRequest]) == 0
    assert stream_counter == 2


@pytest.mark.asyncio
async def test_stream_can_get_cancelled(endpoint: Endpoint) -> None:
    stream_counter = 0

    async_generator = endpoint.stream(DummyRequest)

    async def stream_response() -> None:
        nonlocal stream_counter
        async for event in async_generator:
            # Accessing `ev.property_of_dummy_request` here allows us to validate
            # mypy has the type information we think it has. We run mypy on the tests.
            print(event.property_of_dummy_request)
            stream_counter += 1
            await asyncio.sleep(0.1)

    async def cancel_soon() -> None:
        while True:
            await asyncio.sleep(0.01)
            if stream_counter == 2:
                await async_generator.aclose()

    stream_coro = asyncio.ensure_future(stream_response())
    cancel_coro = asyncio.ensure_future(cancel_soon())

    for i in range(50):
        await endpoint.broadcast(DummyRequest())

    await asyncio.sleep(0.2)
    # Ensure the registration was cleaned up
    assert len(endpoint._queues[DummyRequest]) == 0
    assert stream_counter == 2

    # clean up
    stream_coro.cancel()
    cancel_coro.cancel()


@pytest.mark.asyncio
async def test_stream_cancels_when_parent_task_is_cancelled(endpoint: Endpoint) -> None:
    stream_counter = 0

    async def stream_response() -> None:
        nonlocal stream_counter
        async for event in endpoint.stream(DummyRequest):
            # Accessing `ev.property_of_dummy_request` here allows us to validate
            # mypy has the type information we think it has. We run mypy on the tests.
            print(event.property_of_dummy_request)
            stream_counter += 1
            await asyncio.sleep(0.01)

    task = asyncio.ensure_future(stream_response())

    async def cancel_soon() -> None:
        while True:
            await asyncio.sleep(0.01)
            if stream_counter == 2:
                task.cancel()
                break

    asyncio.ensure_future(cancel_soon())

    for i in range(10):
        await endpoint.broadcast(DummyRequest())

    await asyncio.sleep(0.1)
    # Ensure the registration was cleaned up
    assert len(endpoint._queues[DummyRequest]) == 0
    assert stream_counter == 2


@pytest.mark.asyncio
async def test_wait_for(endpoint: Endpoint) -> None:
    received = None

    async def stream_response() -> None:
        request = await endpoint.wait_for(DummyRequest)
        # Accessing `ev.property_of_dummy_request` here allows us to validate
        # mypy has the type information we think it has. We run mypy on the tests.
        print(request.property_of_dummy_request)
        nonlocal received
        received = request

    asyncio.ensure_future(stream_response())
    await endpoint.broadcast(DummyRequest())

    await asyncio.sleep(0.01)
    assert isinstance(received, DummyRequest)


@pytest.mark.asyncio
async def test_wait_for_can_get_cancelled(endpoint: Endpoint) -> None:

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(endpoint.wait_for(DummyRequest), 0.01)
    await asyncio.sleep(0.01)
    # Ensure the registration was cleaned up
    assert len(endpoint._queues[DummyRequest]) == 0


class RemoveItem(BaseEvent):
    def __init__(self, item: int) -> None:
        super().__init__()
        self.item = item


@pytest.mark.asyncio
async def test_exceptions_dont_stop_processing(capsys: SysCapture,
                                               endpoint: Endpoint) -> None:

    the_set = {1, 3}

    def handle(message: RemoveItem) -> None:
        the_set.remove(message.item)

    endpoint.subscribe(RemoveItem, handle)

    # this call should work
    await endpoint.broadcast(RemoveItem(1))
    await asyncio.sleep(0.05)
    assert the_set == {3}

    captured = capsys.readouterr()
    assert len(captured.err) == 0

    # this call causes an exception
    await endpoint.broadcast(RemoveItem(2))
    await asyncio.sleep(0.05)
    assert the_set == {3}

    captured = capsys.readouterr()
    assert len(captured.err) > 0

    # despite the previous exception this message should get through
    await endpoint.broadcast(RemoveItem(3))
    await asyncio.sleep(0.05)
    assert the_set == set()


def test_pickle_fails() -> None:
    endpoint = Endpoint()

    with pytest.raises(Exception):
        pickle.dumps(endpoint)
