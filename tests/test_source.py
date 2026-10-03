import pytest
import requests
from pydantic import BaseModel, ValidationError

from instastoryhook.source import SchemaError, SourceError, call, make_client, save_session


@pytest.mark.parametrize("transport", ["curl", "requests"])
def test_private_timeout_survives_settings_reload(tmp_path, monkeypatch, transport):
    client = make_client()
    path = tmp_path / "session.json"
    client.set_retry_config(private_transport=transport)
    save_session(client, path)
    client.load_settings(path)
    client.request_timeout = 0
    observed = []

    def send(request, **kwargs):
        observed.append(kwargs["timeout"])
        raise requests.ReadTimeout("sensitive upstream error")

    # Stub the session dispatch itself, regardless of the installed adapter.
    monkeypatch.setattr(client.private, "send", send)
    try:
        with pytest.raises(SourceError, match=r"\(ReadTimeout\)"):
            call(client.private_request, "feed/user/1/story/")
        assert observed == [(10, 30)]
    finally:
        client.private.close()
        client.public.close()


def test_profile_validation_error_is_account_scoped_and_redacted():
    class Profile(BaseModel):
        pk: int

    def resolve():
        return Profile(pk="sensitive upstream data")

    with pytest.raises(SchemaError) as error:
        call(resolve)
    assert isinstance(error.value.__cause__, ValidationError)
    assert "sensitive" not in str(error.value)
