import hashlib
import hmac
import json
import threading

import httpx
import pytest

from instastoryhook.delivery import Webhook, archive, validate_webhook
from instastoryhook.store import Store, exclusive


def test_restart_dedup_and_outbox(tmp_path, event):
    path = tmp_path / "restart.db"
    s = Store(path)
    assert s.add(event, 1)
    s.close()
    s = Store(path)
    assert not s.add(event, 2)
    assert len(s.due(2)) == 1
    rows = list(s.export())
    assert rows[0]["event"] == event
    assert list(s.export(rows[0]["cursor"])) == []
    s.close()


def test_signed_delivery_and_ack(store, event):
    store.add(event, 1)
    received = []

    def receive(request):
        received.append(request)
        return httpx.Response(204)

    hook = Webhook(
        "https://receiver.example",
        "test-secret",
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    assert hook.drain(store, 2) == 1
    assert hook.drain(store, 3) == 0
    request = received[0]
    signed = request.headers["X-Storyhook-Timestamp"].encode() + b"." + request.content
    expected = "sha256=" + hmac.new(b"test-secret", signed, hashlib.sha256).hexdigest()
    assert request.headers["X-Storyhook-Signature"] == expected
    assert request.headers["Idempotency-Key"] == event["id"]
    assert json.loads(request.content) == event
    assert store.status()["events"] == {"delivered": 1}
    hook.close()


def test_failure_backoff_dead_letter_and_replay(store, event):
    store.add(event, 1)
    hook = Webhook(
        "https://receiver.example",
        "secret",
        max_attempts=2,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(503, headers={"Retry-After": "120"})
            )
        ),
    )
    assert hook.drain(store, 2) == 0
    assert store.due(121) == []
    assert len(store.due(122)) == 1
    hook.drain(store, 123)
    assert store.status()["events"] == {"dead": 1}
    assert store.replay() == 1
    assert store.due(123)[0]["attempts"] == 0
    hook.close()


def test_timeout_keeps_event_durable(store, event):
    store.add(event, 1)

    def timeout(request):
        raise httpx.ReadTimeout("may contain secrets", request=request)

    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    hook.drain(store, 2)
    row = store.due(100)[0]
    assert row["last_error"] == "ReadTimeout"
    assert json.loads(row["payload"]) == event
    hook.close()


def test_crash_after_receiver_ack_can_redeliver(store, event, monkeypatch):
    store.add(event, 1)
    ids = []

    def receive(request):
        ids.append(request.headers["Idempotency-Key"])
        return httpx.Response(200)

    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    ack = store.delivered
    monkeypatch.setattr(store, "delivered", lambda _: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        hook.drain(store, 2)
    monkeypatch.setattr(store, "delivered", ack)
    assert hook.drain(store, 3) == 1
    assert ids == [event["id"], event["id"]]
    hook.close()


def test_sink_change_cannot_leak_old_events(store):
    store.bind_webhook("https://first.example")
    with pytest.raises(ValueError):
        store.bind_webhook("https://second.example")


def test_shutdown_does_not_begin_more_deliveries(store, event):
    store.add(event, 1)
    stop = threading.Event()
    stop.set()
    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    assert hook.drain(store, 2, stop=stop) == 0
    assert len(store.due(2)) == 1
    hook.close()


def test_redirect_does_not_receive_secret(store, event):
    store.add(event, 1)
    calls = []

    def receive(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.example"})

    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    assert hook.drain(store, 2) == 0
    assert calls == ["https://receiver.example"]
    assert store.due(100)[0]["last_error"] == "HTTP 302"
    hook.close()


def test_exclusive_worker(tmp_path):
    with exclusive(tmp_path / "worker.lock"), pytest.raises(ValueError):  # noqa: SIM117
        with exclusive(tmp_path / "worker.lock"):
            pass


@pytest.mark.parametrize("url", ["file:///tmp/file", "http://remote.example", "https://u:p@host"])
def test_unsafe_webhook_configuration(url):
    with pytest.raises(ValueError):
        validate_webhook(url)


def test_media_is_archived_without_credentials(tmp_path, event):
    def receive(request):
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"test-image")

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        archive(event, tmp_path, client)
    assert event["story"]["media"]["bytes"] == 10
    assert (tmp_path / (event["story"]["id"] + ".jpg")).read_bytes() == b"test-image"


def test_media_failure_preserves_link_event(tmp_path, event):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
        archive(event, tmp_path, client)
    assert "download_error" in event["story"]["media"]
    assert event["story"]["links"]
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "url",
    ["https://[invalid", "http://127.0.0.1/private", "https://cdninstagram.com.attacker.example/a"],
)
def test_malformed_or_untrusted_media_does_not_discard_event(tmp_path, event, url):
    event["story"]["media"]["url"] = url
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("must not fetch"))
    ) as client:
        archive(event, tmp_path, client)
    assert event["story"]["media"]["download_error"]
    assert event["story"]["links"]
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("status", [204, 503])
def test_large_queue_delivers_or_retries_each_row_once(store, event, status):
    for number in range(120):
        store.add({**event, "id": f"instagram:42:{number}"}, 1)
    ids = []

    def receive(request):
        ids.append(request.headers["Idempotency-Key"])
        return httpx.Response(status)

    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    try:
        assert hook.drain(store, now=2) == (120 if status == 204 else 0)
        assert len(ids) == len(set(ids)) == 120
        assert store.status()["events"] == {("delivered" if status == 204 else "pending"): 120}
        assert not store.due(2)
    finally:
        hook.close()


def test_delivery_deadline_stops_starting_new_requests(store, event, monkeypatch):
    for number in range(3):
        store.add({**event, "id": str(number)}, 1)
    ticks = iter([0, 0, 31])
    monkeypatch.setattr("instastoryhook.delivery.time.monotonic", lambda: next(ticks))
    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204))),
    )
    try:
        assert hook.drain(store, now=2) == 1
        assert store.status()["events"] == {"delivered": 1, "pending": 2}
    finally:
        hook.close()


def test_retry_delay_starts_after_slow_failed_request(store, event, monkeypatch):
    clock = [100]
    monkeypatch.setattr("instastoryhook.delivery.time.time", lambda: clock[0])
    store.add(event, 1)

    def receive(request):
        clock[0] += 20
        return httpx.Response(503)

    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    try:
        hook.drain(store)
        assert not store.due(129)
        assert len(store.due(130)) == 1
    finally:
        hook.close()


def test_oversized_retry_after_is_capped(store, event):
    store.add(event, 1)
    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(503, headers={"Retry-After": "9" * 5000})
            )
        ),
    )
    try:
        assert hook.drain(store, now=2) == 0
        assert not store.due(86401)
        assert len(store.due(86402)) == 1
    finally:
        hook.close()


@pytest.mark.parametrize("cancel", [True, False])
def test_media_deadline_and_shutdown_remove_partial_file(tmp_path, event, monkeypatch, cancel):
    stop = threading.Event()
    clock = [0]
    monkeypatch.setattr("instastoryhook.delivery.time.monotonic", lambda: clock[0])

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"first chunk"
            if cancel:
                stop.set()
            else:
                clock[0] = 121
            yield b"second chunk"

    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=SlowStream()))
    ) as client:
        archive(event, tmp_path, client, stop=stop)
    assert event["story"]["media"]["download_error"] == "ValueError"
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.jpg"))
