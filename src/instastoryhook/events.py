"""Stable JSON envelope; preserve Instagram URL strings and original fields."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from .source import SchemaError


def utc(timestamp):
    return datetime.fromtimestamp(float(timestamp), UTC).isoformat().replace("+00:00", "Z")


def links(item):
    result = []
    for sticker in objects(item.get("story_link_stickers")):
        link = sticker.get("story_link")
        if not isinstance(link, dict):
            continue
        if isinstance(link.get("url"), str):
            result.append({"source": "sticker", **link})
    for cta in objects(item.get("story_cta")):
        for link in objects(cta.get("links")):
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


def objects(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def best(versions):
    def area(version):
        try:
            return max(0, int(version.get("width") or 0)) * max(0, int(version.get("height") or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    valid = [v for v in objects(versions) if isinstance(v.get("url"), str)]
    return max(valid, key=area, default={})


def normalize(item, account, now):
    try:
        story_id = str(item.get("pk") or item["id"].split("_")[0])
        if not story_id.isascii() or not story_id.isdigit():
            raise ValueError("non-numeric story id")
        posted = float(item["taken_at"])
        expires = float(item.get("expiring_at") or posted + 86400)
        media_type = int(item["media_type"])
        images = item.get("image_versions2")
        preview = best(images.get("candidates")) if isinstance(images, dict) else {}
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
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError) as exc:
        raise SchemaError("Story fields changed or a required field is missing") from exc
