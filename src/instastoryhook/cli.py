import argparse
import getpass
import json
import os
import signal
import sys
import threading
from contextlib import ExitStack
from pathlib import Path

from .delivery import Webhook
from .source import Instagram, call, make_client, save_session
from .store import Store, exclusive
from .worker import report, run


def parser():
    p = argparse.ArgumentParser(
        description="Poll Instagram stories into SQLite, JSONL and webhooks"
    )
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    sub = p.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="Create a reusable session interactively")
    login.add_argument("--username")
    login.add_argument(
        "--from-chrome", action="store_true", help="Best-effort one-time cookie import"
    )
    login.add_argument("--cookie-file", type=Path, help="Chrome profile's Cookies database")
    login.add_argument(
        "--session-id", action="store_true", help="Prompt privately for a session ID"
    )
    watch = sub.add_parser(
        "run", help="Poll accounts; first run captures all currently active stories"
    )
    watch.add_argument("accounts", nargs="+", help="Public usernames")
    watch.add_argument(
        "--interval", type=int, default=300, help="Seconds between completed cycles; >=60"
    )
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--download-media", action="store_true")
    watch.add_argument("--quiet", action="store_true", help="No story JSONL on stdout")
    for command in (
        watch,
        sub.add_parser("deliver", help="Deliver due outbox rows without Instagram"),
    ):
        command.add_argument("--webhook", default=os.getenv("STORYHOOK_WEBHOOK_URL"))
        command.add_argument("--max-attempts", type=int, default=12)
    export = sub.add_parser("export", help="Replay stored JSONL with a durable sequence cursor")
    export.add_argument("--after", type=int, default=0)
    sub.add_parser("status", help="Print latest polling health and queue counts")
    replay = sub.add_parser("replay", help="Requeue dead deliveries, or one event by ID")
    replay.add_argument("--id")
    return p


def login(args, session):
    client = make_client(os.getenv("STORYHOOK_PROXY"))
    if session.exists():
        client.load_settings(session)  # Preserve device IDs across manual re-authentication.
    if args.from_chrome:
        try:
            import browser_cookie3
        except ImportError as exc:
            raise ValueError("Install instastoryhook[chrome] for Chrome import") from exc
        jar = browser_cookie3.chrome(
            cookie_file=str(args.cookie_file) if args.cookie_file else None,
            domain_name=".instagram.com",
        )
        cookies = {
            c.name: c.value
            for c in jar
            if c.domain
            in (
                ".instagram.com",
                "instagram.com",
                "www.instagram.com",
            )
        }
        if not cookies.get("sessionid"):
            raise ValueError("No Instagram session in this Chrome profile; pass --cookie-file")
        call(client.login_by_sessionid, cookies["sessionid"])
    elif args.session_id:
        call(client.login_by_sessionid, getpass.getpass("Instagram session ID: "))
    else:
        username = args.username or input("Instagram username: ")
        password = getpass.getpass("Instagram password: ")
        code = getpass.getpass("2FA code if enabled, otherwise Enter: ")
        call(client.login, username, password, verification_code=code)
    save_session(client, session)
    report("session_saved", path=str(session))
    return 0


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    data = args.data_dir.resolve()
    session = data / "session.json"
    try:
        with ExitStack() as stack:
            if args.command not in ("status", "export"):
                stack.enter_context(exclusive(data / "worker.lock"))
            if args.command == "login":
                return login(args, session)
            store = Store(data / "stories.sqlite3")
            stack.callback(store.close)
            if args.command == "status":
                print(json.dumps(store.status()))
                return 0
            if args.command == "export":
                for row in store.export(args.after):
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                return 0
            if args.command == "replay":
                print(json.dumps({"requeued": store.replay(args.id)}))
                return 0
            if args.max_attempts < 1:
                raise ValueError("max-attempts must be positive")
            webhook = None
            if args.webhook:
                webhook = Webhook(
                    args.webhook,
                    os.getenv("STORYHOOK_WEBHOOK_SECRET"),
                    os.getenv("STORYHOOK_WEBHOOK_TOKEN"),
                    args.max_attempts,
                )
                stack.callback(webhook.close)
                store.bind_webhook(args.webhook)
            if args.command == "deliver":
                if not webhook:
                    raise ValueError("deliver requires --webhook or STORYHOOK_WEBHOOK_URL")
                count = webhook.drain(store)
                print(json.dumps({"delivered": count, **store.status()}))
                return 1 if store.status()["events"].get("dead", 0) else 0
            if args.interval < 60:
                raise ValueError("interval must be at least 60 seconds")
            if not session.exists():
                raise ValueError("No saved session; run instastoryhook login first")
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            source = Instagram(session, os.getenv("STORYHOOK_PROXY"))
            return run(
                source,
                store,
                args.accounts,
                args.interval,
                stop,
                webhook,
                args.once,
                data / "media" if args.download_media else None,
                not args.quiet,
            )
    except BrokenPipeError:
        # Events already committed remain available through export or webhook delivery.
        return 1
    except Exception as exc:  # noqa: BLE001 -- Do not leak upstream credentials in tracebacks.
        from .source import SourceError

        message = str(exc) if isinstance(exc, (ValueError, SourceError)) else type(exc).__name__
        report("fatal", error=message)
        return 2


if __name__ == "__main__":
    sys.exit(main())
