"""Stable JSON envelope; preserve Instagram URL strings and original fields."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from .source import SchemaError


def utc(timestamp):
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat().replace("+00:00", "Z")


def links(item):
    result = []
    for sticker in item.get("story_link_stickers") or []:
        link = sticker.get("story_link") or {}
        if isinstance(link.get("url"), str):
            result.append({"source": "sticker", **link})
    for cta in item.get("story_cta") or []:
        for link in cta.get("links") or []:
            if isinstance(link.get("webUri"), str):
                result.append({**link, "source": "cta", "url": link["webUri"]})
    for link in result:
        try:
            parsed = urlsplit(link["url"])
            if parsed.hostname == "l.instagram.com":
                target = parse_qs(parsed.query).get("u", [None])[0]
                if target and urlsplit(target).scheme in ("http", "https"):
                    link["resolved_url"] = target
        except ValueError:
            pass  # Preserve malformed upstream URLs as evidence, never fetch them.
    return result


def best(versions):
    valid = [v for v in versions or [] if isinstance(v.get("url"), str)]
    return max(valid, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0), default={})


def normalize(item, account, now):
    try:
        story_id = str(item.get("pk") or item["id"].split("_")[0])
        if not story_id.isdigit():
            raise ValueError("non-numeric story id")
        posted = float(item["taken_at"])
        expires = float(item.get("expiring_at") or posted + 86400)
        media_type = int(item["media_type"])
        preview = best((item.get("image_versions2") or {}).get("candidates"))
        media = best(item.get("video_versions")) if media_type == 2 else preview
        caption = item.get("caption") or {}
        return {
            "schema_version": 1,
            "type": "instagram.story.discovered",
            "id": f"instagram:{account['id']}:{story_id}",
            "discovered_at": utc(now),
            "account": account,
            "story": {
                "id": story_id,
                "posted_at": utc(posted),
                "expires_at": utc(expires),
                "url": f"https://www.instagram.com/stories/{account['username']}/{story_id}/",
                "caption": caption.get("text") if isinstance(caption, dict) else caption,
                "links": links(item),
                "media": {
                    "type": {1: "image", 2: "video"}.get(media_type, "unknown"),
                    "url": media.get("url"),
                    "thumbnail_url": preview.get("url"),
                    "width": media.get("width"),
                    "height": media.get("height"),
                },
                "raw": item,
            },
        }
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise SchemaError("Story fields changed or a required field is missing") from exc
