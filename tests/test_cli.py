import json

from instastoryhook.cli import main
from instastoryhook.store import Store, exclusive


def test_status_and_export_work_during_worker_lock(tmp_path, event, capsys):
    store = Store(tmp_path / "stories.sqlite3")
    store.add(event, 1)
    store.close()
    with exclusive(tmp_path / "worker.lock"):
        assert main(["--data-dir", str(tmp_path), "status"]) == 0
        assert json.loads(capsys.readouterr().out)["events"] == {"pending": 1}
        assert main(["--data-dir", str(tmp_path), "export"]) == 0
        assert json.loads(capsys.readouterr().out)["event"] == event


def test_invalid_interval_does_not_contact_instagram(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path), "run", "creator", "--interval", "1"]) == 2
    assert "at least 60" in capsys.readouterr().err


def test_missing_session_is_actionable(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path), "run", "creator", "--once"]) == 2
    assert "login first" in capsys.readouterr().err
