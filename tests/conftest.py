import pytest

from instastoryhook.events import normalize
from instastoryhook.store import Store


@pytest.fixture
def item():
    return {
        "pk": "1234567890123456789",
        "taken_at": 1700000000,
        "media_type": 1,
        "caption": {"text": "New role"},
        "image_versions2": {
            "candidates": [
                {"url": "https://s.cdninstagram.com/image.jpg", "width": 1080, "height": 1920},
            ]
        },
        "story_link_stickers": [
            {
                "story_link": {
                    "url": "https://jobs.example",
                    "link_title": "APPLY",
                }
            }
        ],
    }


@pytest.fixture
def event(item):
    return normalize(item, {"id": "42", "username": "public_creator"}, 1700000001)


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "stories.sqlite3")
    yield result
    result.close()
