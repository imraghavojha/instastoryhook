# Validation

Executed locally on 2026-10-03 UTC, using Python 3.13 and Instagrapi 3.0.18.
These are point-in-time checks, not a claim of unattended uptime.

## Live Instagram and pipeline test

The user-authorized logged-in Chrome session was imported once. The actual CLI
then loaded its saved settings, fetched public stories, archived media, persisted
events, and sent them to a local HTTP receiver that verified HMAC signatures,
timestamps and idempotency keys.

| Account | Active stories | New events on first poll | New events on second poll |
| --- | ---: | ---: | ---: |
| zero2sudo | 33 | 33 | 0 |
| nasa | 3 | 3 | 0 |
| github | 0 | 0 | 0 |

- 36 unique webhook events, all 36 signatures valid, all acknowledged with 204.
- 36 media files saved, 6,296,735 bytes total, zero download errors.
- 26 link stickers across the two accounts. All used Instagram redirect wrappers;
  original strings were preserved and destinations decoded without network visits.
- Both completed polling cycles reported healthy status and exited successfully.
- A direct empty-account check confirmed `github` returns `status: "ok"` and
  `reel: null`, matching the strict empty-response fixture.
- Decoded links included Greenhouse, Ashby, Workday, Amazon Jobs, Microsoft
  Careers, and other destinations. No job links were followed or applications sent.
- Saved session file permissions were verified as `0600`.
- No messages, posts, or follow requests were sent. Viewing a story in Chrome for
  initial verification can mark it seen; the collector sends no seen requests.

## Automated checks

33 tests passed initially. Coverage includes strict response validation, exact
URL preservation, legacy links, media selection, public-account filtering,
restart deduplication, signed delivery, backoff and dead-letter replay, a crash
after receiver acceptance, webhook destination binding, worker locking,
credential-free media requests, partial story failures, session permissions,
atomic session replacement, and persisted source cooldown.

Ruff lint and formatting checks passed, and `pip check` found no dependency
conflicts. Compose configuration validated. Container build results and final
test count are recorded below after checking.

## Limits of validation

- No fresh story was posted by a controlled test account during the run. Active
  story discovery and repeat-poll deduplication were tested; real publish-to-hook
  latency and days-long session stability were not measured.
- Browser-session authentication was exercised live. Password/2FA login was not
  exercised because it would require the user's credentials.
- Account challenges, rate limits, and receiver failures use automated tests;
  we did not provoke Instagram restrictions on the user's account.
- Local networking was tested. Moving this session to a cloud IP may require
  manual re-authentication. No scraping method here guarantees access forever.
- No background monitoring service is left running after these bounded tests.
