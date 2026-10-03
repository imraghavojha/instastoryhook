import hashlib
import hmac
import json

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
