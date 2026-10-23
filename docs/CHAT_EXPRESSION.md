# Natural replies, emotion and reaction stickers

Android 0.3.42 / versionCode 45 supports multi-message replies, optional reaction stickers and persistent conversational emotion.

## Behavior

The main model proposes one response plan: one to four text parts, an optional emotion update and at most one sticker ID. The server validates the plan and checks the combined text before delivery. A factual rewrite invalidates the old fragments and sticker. Each delivered part has a durable ID; queued tails are cancelled when the user interrupts, and a restart resumes delivery without regenerating the group. A group schedules one best-effort push, not one push per fragment. A process crash can still lose an external push; this is not an end-to-end exactly-once guarantee.

Emotion is scoped to account and agent, bounded, deduplicated and decays with time. It influences persona context and sticker selection. Auri should not punish sleep, busy periods or slow replies. Expressive quality depends on the configured model; passing protocol tests does not prove human-like conversation quality.

`AURI_CHAT_EXPRESSION_ENABLED=true` enables plans for clients advertising `message_sequence_v1`. `AURI_CHAT_MESSAGE_GAP_SECONDS=1.4` controls the default gap (0.3–5 seconds). Older clients continue receiving one complete reply. Chat settings expose independent continuous-message and sticker switches through authenticated `GET/PUT /v1/persona/expressions`. Android Room migrates from 10 to 11 to retain sticker metadata.

## Reviewed community memes

`server/app/static/expression/community.json` describes 48 familiar cat, dog, question-cat and opossum reactions, including 16 GIFs reviewed on 2026-10-23. This is a curated community set, not a real-time popularity ranking. Selection uses the current message meaning, emotion and recent-use deduplication. No chat text is sent to the upstream sticker collection. Only known IDs and cached same-origin image paths can be sent.

Source: [ChineseBQB](https://github.com/zhaoolee/ChineseBQB), [current index](https://zhaoolee.com/ChineseBQB/catalog/search.json). Its README offers the image/index data for developer use, but does not supply individual rights evidence for every collected image. Images belong to their respective authors; the public source repository includes metadata and an importer, **not third-party image binaries**. Operators should review their intended use and can remove an entry from the manifest to stop future selection.

To explicitly import the reviewed files into your own instance:

```bash
cd server
python scripts/import_community_stickers.py
python scripts/import_community_stickers.py --verify-only
```

The importer checks the source host/path, byte limit, SHA-256, image format, dimensions and animation flag before writing files. It is an operator action, never a network request on a chat's critical path. Re-review additions when updating the set. Keep original sources and attribution. Missing images are excluded from candidates, so disabling an unavailable provider does not block text replies.

Animated GIFs use Coil decoders and preserve aspect ratio; static atlases retain cropped character reactions. Existing character artwork may be supplied through `manifest.json`. Character stickers remain gender-specific; reviewed animal memes use `presentation=any` and are available to both presentations.

## Artwork

The official private deployment has new character artwork with 24 varied backgrounds per presentation, using multiple palettes, viewing angles, gaze directions and activities. Public builds retain placeholders. Operators may supply their own versioned `app/static/portrait/v3/manifest.json`, `female/`, `male/` and `avatar/` assets. Selection never falls back to another presentation; missing new assets do not resurrect a rejected older image set. Use a new version path/ID to invalidate old caches.

## Validation

Local release validation: private server 557 tests, public server 546 tests; Android direct/store each 29 JVM tests, both Debug/Release builds and lintVital passed. An isolated emulator verified cached history, multiple deliveries, image preview, static community memes and changing GIF frames. Six fictional provider cases covered success, boundaries, apology, quiet distress and explicit static/animated meme requests. These checks do not establish universal dialogue quality or physical-device delivery.
