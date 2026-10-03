import json
import random
import sys
import time

from .delivery import archive
from .events import normalize, utc
from .source import AuthRequired, InvalidAccount, RateLimited, SchemaError, SourceError


def report(kind, **fields):
    print(json.dumps({"type": kind, **fields}), file=sys.stderr, flush=True)


def collect(source, store, usernames, media_dir=None, emit=True, stop=None):
    added, errors = 0, 0
    accounts = list(dict.fromkeys(name.removeprefix("@").lower() for name in usernames))
    random.shuffle(accounts)
    for username in accounts:
        if stop and stop.is_set():
            break
        try:
            now = time.time()
            account = store.account(username, now)
            if account is None:
                account = source.resolve(username)
                store.save_account(username, account, now)
            items = source.stories(account)
            fresh = 0
            for item in items:
                if stop and stop.is_set():
                    break
                try:
                    event = normalize(item, account, now)
                except SchemaError as exc:
                    report("story_error", username=username, error=str(exc))
                    errors += 1
                    continue
                if store.contains(event["id"]):
                    continue
                if media_dir:
                    archive(event, media_dir)
                if store.add(event, now):
                    fresh += 1
                    if emit:
                        print(json.dumps(event, ensure_ascii=False), flush=True)
            added += fresh
            report("account_polled", username=username, stories=len(items), new=fresh)
        except InvalidAccount as exc:
            report("account_error", username=username, error=str(exc))
            errors += 1
        # Rate/auth/transport failures stop this cycle, avoiding a request storm.
    return added, errors


def run(
    source, store, usernames, interval, stop, webhook=None, once=False, media_dir=None, emit=True
):
    prior = store.status().get("health") or {}
    next_poll = prior.get("next_poll", 0)
    failures = prior.get("failures", 0)
    while not stop.is_set():
        now = time.time()
        if webhook:
            sent = webhook.drain(store, stop=stop)
            if sent:
                report("webhooks_delivered", count=sent)
        if now >= next_poll:
            try:
                added, errors = collect(source, store, usernames, media_dir, emit, stop)
                failures = 0
                next_poll = time.time() + interval * random.uniform(1, 1.2)
                health = {
                    "status": "degraded" if errors else "ok",
                    "new": added,
                    "account_or_story_errors": errors,
                    "last_poll": utc(time.time()),
                }
                exit_code = 1 if errors else 0
            except AuthRequired as exc:
                store.health({"status": "auth_required", "error": str(exc), "at": utc(now)})
                report("auth_required", error=str(exc))
                return 2
            except SourceError as exc:
                failures += 1
                delay = min(3600, max(interval, 300) * 2 ** min(failures - 1, 6))
                next_poll = time.time() + delay * random.uniform(1, 1.2)
                health = {
                    "status": "rate_limited" if isinstance(exc, RateLimited) else "error",
                    "error": str(exc),
                    "last_attempt": utc(now),
                }
                exit_code = 1
            health.update(next_poll=next_poll, failures=failures)
            store.health(health)
            report("health", **health)
            if webhook:
                webhook.drain(store, stop=stop)
            if once:
                return exit_code
        elif once:
            report("cooldown", next_poll=utc(next_poll))
            return 1
        stop.wait(min(2, max(0.1, next_poll - time.time())))
    return 0
