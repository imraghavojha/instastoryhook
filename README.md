<img src="assets/icon.svg" width="48" height="48" alt="">

# instaStoryHook

Self-hosted Instagram story polling with durable, signed JSON webhooks.

Capture link stickers, captions, timestamps and media from public accounts.
SQLite deduplicates stories across restarts and queues webhook retries. Media
downloads are optional.

[Website](https://imraghavojha.github.io/instastoryhook/) · [Operating guide](docs/usage.md)

## Quick start

Python 3.11+ on Linux or macOS. An Instagram login is required.

```sh
git clone https://github.com/imraghavojha/instastoryhook.git
cd instastoryhook
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

instastoryhook login --username YOUR_MONITOR_ACCOUNT
instastoryhook run nasa --once
instastoryhook run nasa --download-media
```

The first poll captures all active stories; later polls emit unseen stories.
Events go to stdout as JSONL and persist in `data/`. Stop with Ctrl-C.

## Webhooks

```sh
export STORYHOOK_WEBHOOK_URL='https://your-server.example/stories'
export STORYHOOK_WEBHOOK_SECRET='replace-with-a-long-random-secret'
instastoryhook run nasa --quiet --download-media
```

Each POST contains one `instagram.story.discovered` event. Verify its
HMAC-SHA256 signature, reject stale timestamps, deduplicate by `Idempotency-Key`,
and persist the event before responding with 2xx. Delivery is at-least-once until
the retry limit; failed events can be replayed.
See the [delivery contract](docs/usage.md#send-to-an-http-pipeline) and
[example receiver](examples/receiver.py).

## Operation

This is polling, with a default interval of five minutes plus jitter. Stories
that expire before a successful poll cannot be recovered. Instagram can throttle
or challenge unofficial API sessions; reauthentication is manual.

Keep `data/` private and persistent. Run one polling worker per Instagram login.
Use `instastoryhook status` to inspect health, `export` to recover stored events,
and `replay` to requeue failed deliveries.

The [operating guide](docs/usage.md) covers Chrome session import, Docker,
webhook verification, retries and storage.

## Development

```sh
pip install -e '.[dev]'
pytest -q
ruff check src tests examples
```

[Validation](docs/validation.md) · [Implementation research](docs/research.md)
