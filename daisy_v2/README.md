# Daisy V2

Daisy V2 is a clean Python rewrite of the Burmese Discord music bot. It keeps
the catalogue, playback, queue, playlist, history, and browsing features in one
cache-first application.

## What is different

- A public catalogue database is separate from Discord user data.
- The authorized Blogspot, pCloud, and MediaFire indexer writes directly to the
  V2 catalogue.
- Every pCloud/YouTube track is downloaded to a bounded temporary disk cache,
  validated, and only then handed to FFmpeg/Discord. The first track can show a
  short buffering wait, but it is not played from an unreliable live pCloud URL.
- Each Discord server has one lock-protected player actor instead of mutating
  private `asyncio.Queue` internals.
- `/join` is available as an explicit fallback when Discord does not expose the
  requester's voice state to the bot.

## Current V2 commands

`/join`, `/sync`, `/search`, `/play`, `/artists`, `/album`, `/youtube`,
`/random`, `/randomalbum`, `/queue`, `/move`, `/nowplaying`, `/pause`, `/resume`, `/skip`,
`/back`, `/repeat`, `/clearqueue`, `/stop`, `/leave`, `/favorite`,
`/favorites`, `/playfavorite`, `/unfavorite`, `/playlist create|add|remove|play`, `/playlists`,
`/history`, `/recent`, `/topsongs`, `/topartists`, `/mystats`, and `/status`.

Artist browsing, full-album playback, playlists, history, and a safe incremental
catalogue sync are already present.

## First local run

From the repository root:

```powershell
py -m venv .venv-v2
.venv-v2\Scripts\Activate.ps1
pip install -r requirements-v2.txt
Copy-Item .env.v2.example .env
python -m daisy_v2
```

Keep `.env`, `cookies.txt`, and every `data/` directory out of Git. To build or
resume the catalogue, use the authorized indexer:

```powershell
python -m daisy_v2.indexer --all
```

## Safe VPS rollout later

Use a dedicated Discord bot token/application for this service and do not run
two copies with the same token at the same time.

[`daisy-v2.service.example`](daisy-v2.service.example) is a safe starting point
for the second service after local testing succeeds.
