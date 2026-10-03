<img src="assets/icon.svg" width="64" height="64" alt="">

# instaStoryHook

Watch public Instagram accounts and send each new story to your pipeline as JSON.
One Python process and SQLite. No hosted scraping service or framework required.

Captures exact link-sticker URLs, legacy swipe-up links, captions, timestamps,
image/video URLs, and original story fields. Optional media downloads preserve
files before their signed URLs expire. Deduplicates across restarts and retries
signed HTTP webhooks through a durable outbox.

**This is polling, not an Instagram push webhook.** Default: 300 seconds between
completed cycles, plus 0–20% jitter. `--interval 60` trades more requests for lower
latency. Fetching, downloads, throttling, and outages add delay. Stories deleted
or expired before a successful poll cannot be recovered.

## Start locally

Python 3.11+ on Linux or macOS. Windows users can run the Docker image.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
instastoryhook login --username YOUR_MONITOR_ACCOUNT
instastoryhook run zero2sudo nasa --once
instastoryhook run zero2sudo --interval 300 --download-media
```

Login prompts privately for the password and optional 2FA code. Passwords are not
saved. The session and device settings go to `data/session.json` with mode `0600`.
The worker reuses these without logging in each cycle. You can also place an
existing Instagrapi settings file at that path.

The first poll emits all currently active stories; later polls emit only unseen
IDs. Stop with Ctrl-C. Logs go to stderr; story JSONL goes to stdout. `--quiet`
disables story stdout but still persists and delivers events.

### Use an existing Chrome login

This worked in live testing, but browser sessions can be rejected by the mobile
API later. Import once, then reuse the saved settings:

```sh
pip install -e '.[chrome]'
instastoryhook login --from-chrome --cookie-file '/path/to/Chrome/Profile/Cookies'
```

On macOS, a typical path is
`~/Library/Application Support/Google/Chrome/Profile 2/Cookies`. Choose the
profile actually logged into Instagram. OS keychain access may be needed.
`instastoryhook login --session-id` instead prompts privately for a session ID.
Chrome is only needed for the import, never in the deployed worker.

Use a dedicated account for continuous monitoring. Instagram can throttle or
challenge unofficial API sessions, including the browser session used for import.
Resolve challenges in Instagram, then run `login` manually. The collector does
not solve challenges or rotate accounts.

## Send to an HTTP pipeline

```sh
export STORYHOOK_WEBHOOK_URL='https://your-pipeline.example/stories'
export STORYHOOK_WEBHOOK_SECRET='a-long-random-secret-shared-with-your-receiver'
# Optional bearer authentication:
export STORYHOOK_WEBHOOK_TOKEN='your-receiver-token'
instastoryhook run zero2sudo --quiet --download-media
```

Each POST body is one event. A shortened example:

```json
{
  "schema_version": 1,
  "type": "instagram.story.discovered",
  "id": "instagram:123:456",
  "discovered_at": "2026-10-03T05:00:00Z",
  "account": {"id": "123", "username": "creator"},
  "story": {
    "id": "456",
    "posted_at": "2026-10-03T04:59:00Z",
    "expires_at": "2026-10-04T04:59:00Z",
    "url": "https://www.instagram.com/stories/creator/456/",
    "caption": null,
    "links": [{"source": "sticker", "url": "https://jobs.example/apply?ref=ig"}],
    "media": {"type": "image", "url": "https://cdn.example/story.jpg"},
    "raw": {}
  }
}
```

`raw` contains the original item, not login credentials. API-supplied link strings
are preserved exactly. For Instagram redirect wrappers, `resolved_url` contains
the decoded `u` parameter. The hook never visits job links. Text baked into
pictures/video needs downstream OCR/transcription.

The receiver must:

1. Verify `X-Storyhook-Signature`: `sha256=` plus the hex HMAC-SHA256 of
   `X-Storyhook-Timestamp + "." + raw_request_body`, using the shared secret.
2. Reject timestamps outside a reasonable window, such as five minutes.
3. Deduplicate using `Idempotency-Key`, equal to the JSON event `id`.
4. Durably enqueue before returning a 2xx response.

Delivery is **at-least-once until the retry limit**, not exactly once. A crash
after receiver acceptance but before the local acknowledgement can duplicate a
delivery. Non-2xx and network errors retry with exponential backoff from 10
seconds to one hour. Numeric `Retry-After` is honored up to 24 hours. After 12
attempts a row becomes `dead` and remains available for replay. Redirects are not
followed. HTTPS is required except for localhost.

Try [the example receiver](examples/receiver.py) with the same secret in both
terminals: `python examples/receiver.py`. Set the hook URL to
`http://127.0.0.1:8080/stories`. Its SQLite inbox demonstrates durable deduplication.

A database binds to its first webhook URL to avoid accidentally sending an old
queue to a different destination. Use another `--data-dir` for another sink, or
consume exports to migrate existing events. You can copy the session file to a
new data directory; run only one polling worker per Instagram login.

## Storage, replay, and health

```sh
instastoryhook status
instastoryhook export --after 0 > captured.jsonl
instastoryhook replay                         # requeue dead deliveries
instastoryhook replay --id 'instagram:123:456' # requeue a specific event
instastoryhook deliver                        # attempt up to 50 due deliveries
```

Exports contain `{"cursor": 1, "event": {...}}`. Save the cursor after downstream
work commits and pass it to `--after` next time. stdout during `run` is best-effort;
SQLite lets you recover missed stdout. In export-only mode events remain pending
until a webhook delivers them.

The worker checks its outbox between polls, including during Instagram backoff.
`--once` makes one poll cycle and bounded delivery passes, then exits. It honors
the saved next-poll time; an immediate repeat reports `cooldown`. `deliver` needs
no Instagram session, so it can drain captured data after auth expires. Repeat
it for larger queues or pending retries.

Exit codes: `0` successful cycle/clean stop, `1` degraded cycle or cooldown, `2`
configuration/auth failure. `status` prints queue counts and last poll health.
Check timestamps too, to detect a stopped worker. Auth failures exit. Other source
errors pause the cycle and back off, persisting cooldown across restarts. Public
profile IDs are cached for 24 hours; private accounts are rejected on resolution
and whenever the reel says private.

`--download-media` saves the best available image/video under `data/media/` and
adds `local_path` and `bytes` to the event. Files are local to the worker; remote
receivers need a shared volume or should fetch source URLs promptly. Downloads
are limited to 100 MiB and are best-effort: failures add `download_error` without
dropping links. Failed media downloads are not retried automatically. Downloads
precede event persistence, so slow downloads increase latency.

Persist the entire `data/` directory. Nothing is pruned automatically. Keep its
session, database, and backups private. One writer runs per data directory;
status and export remain available while it runs.

## Docker

Bootstrap a session locally first, then:

```sh
docker build -t instastoryhook .
docker run --rm --init \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/data" \
  -e STORYHOOK_WEBHOOK_URL -e STORYHOOK_WEBHOOK_SECRET \
  instastoryhook run zero2sudo --download-media --quiet
```

For Compose, copy `.env.example` to `.env`, set `STORYHOOK_UID` and `STORYHOOK_GID`
to `id -u` and `id -g`, fill any webhook settings, then run `docker compose up
--build`. Edit the `command` account list in `compose.yaml`. Compose reads `.env`;
the Python CLI reads environment variables directly. No inbound ports are needed.
Containers run as non-root; the volume must be writable by that user, including
session file replacements. Automatic restarts are off to prevent auth-error loops.

Moving a session to a VPS changes its network origin and may require manual login
again. Keep a stable egress address. `STORYHOOK_PROXY` sets an optional proxy for
Instagram requests; webhooks and media use normal HTTPX networking. Instagram
session portability is not guaranteed.

## Development and evidence

```sh
pip install -e '.[dev]'
pytest -q
ruff check src tests examples
```

See [research and the Claude Opus 5.5 design review](docs/research.md) and
[validation results](docs/validation.md). The Instagram dependency is pinned
because unofficial endpoints can change without notice. No official API offers
this project's arbitrary public-account story subscription. Check upstream
behavior when upgrading; never interpret an auth error as an empty story list.
