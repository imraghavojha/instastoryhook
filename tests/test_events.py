import pytest

from instastoryhook.events import normalize
from instastoryhook.source import AuthRequired, InvalidAccount, SchemaError, story_items


def test_exact_links_and_raw_fields_survive(item, event):
    assert event["story"]["links"][0]["url"] == "https://jobs.example"
    assert event["story"]["raw"] == item
    assert event["id"] == "instagram:42:1234567890123456789"
    assert event["story"]["expires_at"] == "2023-11-15T22:13:20Z"


def test_legacy_and_wrapped_links(item):
    original = "https://l.instagram.com/?u=https%3A%2F%2Fjobs.example%2F%3Fa%3D1%26b%3D2"
    item["story_cta"] = [{"links": [{"webUri": original}]}]
    event = normalize(item, {"id": "42", "username": "creator"}, 1700000001)
    link = event["story"]["links"][1]
    assert link["source"] == "cta"
    assert link["url"] == original
    assert link["resolved_url"] == "https://jobs.example/?a=1&b=2"


def test_video_chooses_highest_resolution(item):
    item.update(
        media_type=2,
        video_versions=[
            {"url": "https://s.cdninstagram.com/small.mp4", "width": 100, "height": 100},
            {"url": "https://s.cdninstagram.com/big.mp4", "width": 1080, "height": 1920},
        ],
    )
    event = normalize(item, {"id": "42", "username": "creator"}, 1700000001)
    assert event["story"]["media"]["url"].endswith("big.mp4")


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"status": "ok"},
        {"status": "fail", "reel": None},
        {"status": "ok", "reel": {}},
        {"status": "ok", "reel": {"items": None}},
        {"status": "ok", "reel": {"items": [None]}},
        [],
    ],
)
def test_malformed_response_is_not_silent_empty(response):
    with pytest.raises(SchemaError):
        story_items(response)


@pytest.mark.parametrize(
    "response",
    [
        {"status": "ok", "reel": None},
        {"status": "ok", "reel": {"items": []}},
    ],
)
def test_explicit_empty(response):
    assert story_items(response) == []


def test_challenge_even_with_ok_status():
    with pytest.raises(AuthRequired):
        story_items({"status": "ok", "reel": None, "message": "challenge_required"})


def test_private_reel_rejected():
    with pytest.raises(InvalidAccount):
        story_items({"status": "ok", "reel": {"items": [], "user": {"is_private": True}}})


def test_malformed_story_fails_explicitly(item):
    del item["taken_at"]
    with pytest.raises(SchemaError):
        normalize(item, {"id": "42", "username": "creator"}, 1700000001)
