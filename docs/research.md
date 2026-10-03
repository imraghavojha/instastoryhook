# Story access research

Research date: 2026-10-02. This component watches public accounts using an
authenticated Instagram viewer. It cannot guarantee immediate delivery or
uninterrupted access to Instagram.

## Candidates

| Approach | Evidence | Decision |
| --- | --- | --- |
| Meta Instagram API | [Meta's official collection](https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api) covers authorized professional accounts and limited discovery. It is not a general public-account story feed. | Does not cover this project's arbitrary-account requirement. |
| Instagrapi | [Current source](https://github.com/subzeroid/instagrapi), saved sessions, mobile story endpoint, and link-sticker extraction. Version 3.0.18 installed for evaluation. | Leading candidate, subject to live validation and design review. |
| Instaloader | [Story API](https://instaloader.github.io/module/instaloader.html) requires login. [Issue 2720](https://github.com/instaloader/instaloader/issues/2720) reports a null story-tray failure; [issue 2651](https://github.com/instaloader/instaloader/issues/2651) reports throttling. | Useful downloader, but does not remove session or endpoint fragility. |
| Other private API libraries | [ping/instagram_private_api](https://github.com/ping/instagram_private_api) and [dilame/instagram-private-api](https://github.com/dilame/instagram-private-api) expose unofficial APIs too. | No demonstrated reliability advantage over the actively maintained Python candidate. |
| Browser automation | Can view stories using an existing login. | Useful for verification. A browser process and UI selectors add deployment and maintenance costs to the worker. |
| Hosted scraping API | Moves account/session management to a vendor. | Adds a paid external dependency; outside the requested standalone design. |

## Findings

- [Instagrapi's production guidance](https://subzeroid.github.io/instagrapi/latest/usage-guide/best-practices/)
  treats anonymous requests as opportunistic and warns about session expiry,
  challenges, and rate limits. A browser session working once does not establish
  long-term mobile session reliability.
- [The story implementation](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/mixins/story.py)
  reads `feed/user/{id}/story/`. Its high-level helper treats a missing reel as
  empty. Our adapter should validate response shape before accepting emptiness.
- [The extractor](https://github.com/subzeroid/instagrapi/blob/master/instagrapi/extractors.py)
  handles `story_link_stickers[].story_link.url` and legacy
  `story_cta[].links[].webUri`. Preserve URLs exactly, including query strings.
- [Realtime support](https://subzeroid.github.io/instagrapi/latest/usage-guide/realtime/)
  is experimental. It does not establish a dependable subscription to every
  new story from any public account. Polling is the baseline.
- Text baked into an image/video requires OCR/transcription. Captions and link
  stickers are structured metadata. Do not claim complete text extraction from
  metadata alone.

## Initial live probe

Chrome displayed an active `zero2sudo` story. A read-only Instagrapi probe using
the authorized Chrome session returned `status=ok`, 33 stories, and 25 link
stickers. No password was requested and no story was posted or messaged.
The browser viewer itself may mark viewed stories as seen. The collector does
not call a seen endpoint. Credentials and captured content stay outside Git.

Design review and final validation are recorded below when completed.
