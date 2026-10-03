# Validation

Executed on 2026-10-03 UTC using Instagrapi 3.0.18. These are point-in-time
checks, not a claim of unattended uptime or guaranteed Instagram access.

## Five-account live test

The saved, user-authorized Instagram session was reused without a new login.
The actual CLI fetched stories from five public accounts, downloaded media,
persisted events to SQLite, and posted them to a local HTTP receiver. Each
receiver request was checked for its HMAC signature, timestamp and idempotency
key. The receiver deliberately returned `503` on each event's first attempt and
`204` on its retry.

| Account | Active stories | First-poll events | Repeat-poll events |
| --- | ---: | ---: | ---: |
| zero2sudo | 32 | 32 | 0 |
| nasa | 3 | 3 | 0 |
| natgeo | 4 | 4 | 0 |
| github | 0 | 0 | 0 |
| microsoft | 0 | 0 | 0 |

Two independent runs exercised this sequence, including one after the reliability
fixes. The final run verified:

- 39 unique events, 29 extracted links, and zero repeat-poll duplicates.
- 39 media files: 30 videos and 9 images, totaling 14,314,327 bytes. Each stored
  byte count matched the downloaded file size; no download errors occurred.
- 78 signed webhook attempts, 39 deliberate first-attempt failures, and 39
  successful retries. The queue ended with every event delivered.
- `run --once` exited `1` while webhook retries were pending, even though all
  account polls succeeded. A separate `deliver` process recovered the queue and
  exited `0` without making Instagram requests.
- An immediate restart respected the saved source cooldown and exited `1`.
  After waiting for the scheduled time, the repeat poll exited `0`.
- Public accounts with no active stories were reported as successful empty
  results. Every account had an individual successful health record.
- Session and test data stayed in ignored local directories. No job links were
  visited, and the collector sent no messages, posts, follows or story-seen calls.

The original implementation was also tested earlier that day with 33 stories
from `zero2sudo`, 3 from `nasa`, and none from `github`. The changed count reflects
stories available at each poll, not a fixed fixture.

## Automated regression checks

66 tests pass on macOS with Python 3.13 and in the Linux container with Python
3.12. Ruff lint and formatting checks pass. Tests cover:

- Mixed public, private, missing, empty and malformed accounts; case/`@` aliases;
  cached profile expiry; account identity; and deduplication across restarts.
- Auth and rate-limit failures stopping further requests while keeping already
  captured events; persisted cooldown; account-specific schema errors allowing
  other accounts to continue; redacted profile-validation errors.
- Malformed story siblings and optional fields, exact sticker and legacy URLs,
  decoded Instagram wrappers, media selection and invalid story IDs.
- Signed delivery, queues of 120 events, deadline enforcement, retry delays from
  request completion, oversized `Retry-After`, timeouts, dead-letter replay,
  destination binding, and crash-after-acceptance redelivery.
- Malformed media URLs, filesystem failures, shutdown during streaming, slow
  streams, temporary-file cleanup, and credential-free media requests.
- Private request timeouts after settings reload for both curl and requests
  transports. Tests stub session dispatch so neither can reach the network.
- Worker locking, concurrent status/export, missing databases, invalid CLI
  usernames, unknown replay IDs, private session permissions and atomic saves.

Claude Opus 5.5 reviewed the implementation and the final fixes. Its final
read-only review found no publication blockers and independently passed all 66
tests, lint and formatting. One earlier reviewer timeout probe unintentionally
made an unauthenticated Instagram request without saved credentials; subsequent
review tests blocked network access.

## Package and container

The wheel builds, `pip check` reports no dependency conflicts, and Compose
configuration validates. Docker builds successfully. CLI help and SQLite writes
work as the image's non-root user, UID 10001. The Linux container also passes the
full test suite. No Instagram credentials were included in the image.

The previous Docker-daemon blocker is resolved. GitHub Actions runs Python 3.11
and 3.13 tests plus a Linux container build and CLI smoke test on pushes.

## Publication check and local artifacts

Gitleaks scanned the reachable commit history and found no secrets. Tracked
files contain no session, cookies, captured event bodies, downloaded media,
databases or local environment configuration. The Docker build context is
restricted to package metadata and source files.

Both live-run scripts, receiver logs, session copies, downloaded media, SQLite
queues and aggregate results remain ignored under `data/validation/` and
`data/validation-final/` in the testing worktree. The prior working database and
session in the original checkout were left unchanged. The test queues bind to
local test receivers and are evidence, not production delivery queues.

## Limits

- No controlled account posted a fresh story during the test. Existing-story
  discovery and duplicate suppression passed; publish-to-hook latency and
  days-long session stability were not measured.
- Browser-session reuse was exercised. Password/2FA login was not exercised.
- Challenges and throttling use automated tests. No restrictions were provoked
  deliberately on the authenticated account.
- Cloud-IP session portability was not tested and may require manual login.
- The process polls; it does not receive instant push notifications. Upstream
  endpoints and session acceptance can change.
- No background collector or test receiver was left running after validation.
