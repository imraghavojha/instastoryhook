"""The only module coupled to Instagram's unofficial API."""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from instagrapi import Client
from instagrapi import exceptions as ig_errors
from pydantic import ValidationError


class SourceError(Exception):
    pass


class AuthRequired(SourceError):
    pass


class RateLimited(SourceError):
    pass


class InvalidAccount(SourceError):
    pass


class SchemaError(SourceError):
    pass


def save_session(client, path: Path):
    """Atomic replace; credentials never pass through stdout or a permissive file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".session-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(client.get_settings(), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def raise_challenge(_client, error):
    # Suppress instagrapi's default interactive/automatic challenge resolution.
    raise error


def make_client(proxy=None):
    logger = logging.getLogger("instastoryhook.instagram")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    client = Client(proxy=proxy, logger=logger, session_retry_total=0)
    client.handle_exception = raise_challenge
    original_request = client.private.request

    def request(method, url, **kwargs):
        # Upstream private GET/POST calls omit timeouts. The session survives
        # settings reloads, unlike its adapters. Cover login and polling here.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (10, 30)
        return original_request(method, url, **kwargs)

    client.private.request = request
    return client


def call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (
        ig_errors.LoginRequired,
        ig_errors.ClientLoginRequired,
        ig_errors.ChallengeError,
        ig_errors.ChallengeRequired,
        ig_errors.FeedbackRequired,
    ) as exc:
        raise AuthRequired("Instagram requires manual login or account review") from exc
    except (
        ig_errors.PleaseWaitFewMinutes,
        ig_errors.ClientThrottledError,
        ig_errors.RateLimitError,
    ) as exc:
        raise RateLimited("Instagram throttled requests; polling will back off") from exc
    except (ig_errors.UserNotFound, ig_errors.ClientNotFoundError) as exc:
        raise InvalidAccount("Instagram account was not found or is unavailable") from exc
    except ValidationError as exc:
        raise SchemaError(
            "Instagram profile fields changed or a required field is missing"
        ) from exc
    except Exception as exc:
        # Upstream exception messages may contain request/session details.
        raise SourceError(f"Instagram request failed ({type(exc).__name__})") from exc


def story_items(response):
    if not isinstance(response, dict):
        raise SchemaError("Instagram returned a non-object response")
    message = str(response.get("message", "")).lower()
    if response.get("challenge") or any(
        x in message
        for x in (
            "login_required",
            "challenge_required",
            "feedback_required",
        )
    ):
        raise AuthRequired("Instagram requires manual login or account review")
    if any(x in message for x in ("please wait", "rate_limit", "throttl")):
        raise RateLimited("Instagram throttled requests")
    if response.get("status") != "ok" or "reel" not in response:
        raise SchemaError("Instagram story response lacks status=ok or reel")
    reel = response["reel"]
    if reel is None:
        return []
    if not isinstance(reel, dict) or not isinstance(reel.get("items"), list):
        raise SchemaError("Instagram story reel lacks an items array")
    user = reel.get("user")
    if user is not None and not isinstance(user, dict):
        raise SchemaError("Instagram story reel has a malformed user")
    if (user or {}).get("is_private"):
        raise InvalidAccount("Only public accounts are supported")
    # Validate each item in normalize so a bad sibling cannot drop valid stories.
    return reel["items"]


def username(value):
    value = value.removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9_.]{1,30}", value):
        raise InvalidAccount("Use an Instagram username, not a URL")
    return value


class Instagram:
    def __init__(self, session: Path, proxy=None):
        self.session = session
        self.client = make_client(proxy)
        self.client.load_settings(session)
        if not self.client.user_id:
            raise AuthRequired("Session has no authenticated user; run login first")

    def resolve(self, name):
        name = username(name)
        user = call(self.client.user_info_by_username_v1, name)
        if user.is_private:
            raise InvalidAccount(f"@{name} is private; only public accounts are supported")
        return {"id": str(user.pk), "username": user.username}

    def stories(self, account):
        result = call(self.client.private_request, f"feed/user/{account['id']}/story/")
        items = story_items(result)
        save_session(self.client, self.session)
        return items
