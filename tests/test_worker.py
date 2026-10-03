import threading

import pytest

from instastoryhook.source import AuthRequired, RateLimited, save_session
from instastoryhook.worker import collect, run


class Source:
    def __init__(self, items=None, error=None):
        self.items, self.error = items or [], error
        self.resolved = 0
        self.polled = 0

    def resolve(self, username):
        self.resolved += 1
        return {"id": "42", "username": username}

    def stories(self, account):
        self.polled += 1
        if self.error:
            raise self.error
        return self.items


def test_duplicate_poll_uses_cached_account_and_no_duplicate_events(store, item):
    source = Source([item])
    assert collect(source, store, ["creator"], emit=False) == (1, 0)
    assert collect(source, store, ["creator"], emit=False) == (0, 0)
    assert source.resolved == 1
    assert len(list(store.export())) == 1


def test_bad_story_does_not_drop_good_sibling(store, item):
    assert collect(Source([{}, item]), store, ["creator"], emit=False) == (1, 1)


def test_auth_failure_stops_remaining_accounts(store):
    source = Source(error=AuthRequired("reauthenticate"))
    code = run(source, store, ["first", "second"], 300, threading.Event(), once=True)
    assert code == 2
    assert source.polled == 1
    assert store.status()["health"]["status"] == "auth_required"


def test_throttling_cooldown_survives_restart(store):
    source = Source(error=RateLimited("wait"))
    run(source, store, ["creator"], 300, threading.Event(), once=True)
    assert store.status()["health"]["status"] == "rate_limited"
    restarted = Source()
    assert run(restarted, store, ["creator"], 300, threading.Event(), once=True) == 1
    assert restarted.polled == 0


def test_session_save_private_and_atomic(tmp_path):
    class Client:
        def get_settings(self):
            return {"test": "credential"}

    path = tmp_path / "session.json"
    save_session(Client(), path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".session-*"))


def test_session_failure_preserves_existing_file(tmp_path):
    class Client:
        def get_settings(self):
            raise ValueError("failed before replace")

    path = tmp_path / "session.json"
    path.write_text("original")
    with pytest.raises(ValueError):
        save_session(Client(), path)
    assert path.read_text() == "original"


@pytest.mark.parametrize("status,exit_code", [(204, 0), (503, 1)])
def test_once_reports_incomplete_webhook_delivery(store, item, status, exit_code):
    import httpx

    from instastoryhook.delivery import Webhook

    hook = Webhook(
        "https://receiver.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))),
    )
    try:
        assert (
            run(
                Source([item]),
                store,
                ["creator"],
                60,
                threading.Event(),
                webhook=hook,
                once=True,
                emit=False,
            )
            == exit_code
        )
    finally:
        hook.close()
