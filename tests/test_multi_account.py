"""Exercise mixed accounts through the real source parser, worker and SQLite store."""

import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from instastoryhook.source import AuthRequired, Instagram, RateLimited
from instastoryhook.store import Store
from instastoryhook.worker import collect, run


class Client:
    def __init__(self, item):
        self.users = {
            "first": ("11", False),
            "second": ("22", False),
            "empty": ("33", False),
            "private": ("44", True),
        }
        self.reels = {
            "11": {"items": [None, item]},
            "22": {"items": [deepcopy(item)]},
            "33": None,
        }
        self.resolved = []
        self.polled = []

    def user_info_by_username_v1(self, username):
        from instagrapi.exceptions import UserNotFound

        self.resolved.append(username)
        if username not in self.users:
            raise UserNotFound()
        pk, private = self.users[username]
        return SimpleNamespace(pk=pk, username=username, is_private=private)

    def private_request(self, endpoint):
        account_id = endpoint.split("/")[2]
        self.polled.append(account_id)
        return {"status": "ok", "reel": self.reels[account_id]}

    def get_settings(self):
        return {}


def source_for(tmp_path, item):
    source = Instagram.__new__(Instagram)
    source.session = tmp_path / "session.json"
    source.client = Client(item)
    return source


def test_mixed_accounts_keep_valid_stories_and_deduplicate_after_restart(tmp_path, item):
    source = source_for(tmp_path, item)
    path = tmp_path / "stories.sqlite3"
    accounts = ["@FIRST", "first", "second", "empty", "private", "missing"]
    store = Store(path)
    try:
        assert collect(source, store, accounts, emit=False) == (2, 3)
        events = [row["event"] for row in store.export()]
        assert {e["account"]["id"] for e in events} == {"11", "22"}
        # Account identity remains part of the key even if upstream returns the same story ID.
        assert len({e["id"] for e in events}) == 2
        assert all(e["story"]["links"] for e in events)
        assert sorted(source.client.polled) == ["11", "22", "33"]
    finally:
        store.close()
    source.client.resolved.clear()
    store = Store(path)
    try:
        assert collect(source, store, accounts, emit=False) == (0, 3)
        assert sorted(source.client.resolved) == ["missing", "private"]
        assert len(list(store.export())) == 2
    finally:
        store.close()


def test_expired_account_cache_refreshes_identity(store, tmp_path, item):
    source = source_for(tmp_path, item)
    store.save_account("first", {"id": "999", "username": "first"}, 0)
    assert collect(source, store, ["first"], emit=False) == (1, 1)
    assert source.client.resolved == ["first"]
    assert next(store.export())["event"]["account"]["id"] == "11"


@pytest.mark.parametrize("error,code", [(AuthRequired("login"), 2), (RateLimited("wait"), 1)])
def test_session_failure_keeps_prior_account_and_stops_later_accounts(
    store, item, monkeypatch, error, code
):
    monkeypatch.setattr("instastoryhook.worker.random.shuffle", lambda values: None)

    class Source:
        def __init__(self):
            self.polled = []

        def resolve(self, username):
            return {"id": username, "username": username}

        def stories(self, account):
            self.polled.append(account["id"])
            if account["id"] == "22":
                raise error
            return [item]

    source = Source()
    assert (
        run(source, store, ["11", "22", "33"], 60, threading.Event(), once=True, emit=False) == code
    )
    assert source.polled == ["11", "22"]
    assert next(store.export())["event"]["account"]["id"] == "11"
    assert store.status()["events"] == {"pending": 1}


def test_malformed_account_does_not_block_valid_account(store, tmp_path, item):
    source = source_for(tmp_path, item)
    source.client.reels["11"]["user"] = "unexpected"
    assert (
        run(source, store, ["first", "second"], 60, threading.Event(), once=True, emit=False) == 1
    )
    assert store.status()["health"]["status"] == "degraded"
    assert store.status()["health"]["accounts"]["first"]["status"] == "error"
    assert store.status()["health"]["accounts"]["second"]["status"] == "ok"
    assert store.status()["events"] == {"pending": 1}


def test_media_directory_failure_still_persists_links(store, tmp_path, item):
    source = source_for(tmp_path, item)
    folder = tmp_path / "not-a-directory"
    folder.write_text("existing file")
    assert collect(source, store, ["second"], media_dir=folder, emit=False) == (1, 0)
    story = next(store.export())["event"]["story"]
    assert story["links"][0]["url"] == "https://jobs.example"
    assert story["media"]["download_error"] == "FileExistsError"
