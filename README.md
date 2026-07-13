# Daisy Discord Music Bot

An authorized, self-hosted Discord bot that indexes Burmese music posts,
catalogues their pCloud folders, and streams tracks with FFmpeg.

## Setup (Windows)

1. Install Python 3.11+ and FFmpeg (`winget install Gyan.FFmpeg`).
2. Create a Discord bot in the Discord Developer Portal and invite it with
   `bot` and `applications.commands` scopes plus Connect/Speak permissions.
3. Run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

4. Put the token in `.env`. Adding your test server ID as `DISCORD_GUILD_ID`
   makes slash-command updates appear immediately.
5. Test the catalogue crawler with `python -m music.crawl --max-posts 10`.
6. Start with `python bot.py`, then run `/sync` in Discord.

To initialize every Blogspot post without waiting for every pCloud folder:

```powershell
python -m music.crawl --all --posts-only
```

Refresh all Blogspot cover/source metadata:

```powershell
python -m music.crawl --all --posts-only --refresh
```

Then index every pending pCloud folder (safe to stop and resume):

```powershell
python -m music.index_pcloud --concurrency 6
```

Resume the normalized artist metadata migration:

```powershell
python -m music.index_pcloud --refresh-missing --concurrency 12
```

Use `--retry-failed` later to retry albums whose links were unavailable.

## Current commands

- `/sync`: index 100 recent posts and their pCloud tracks (DJ/admin only)
- `/search`: browse all matching tracks with pages and an exact-play dropdown
- `/youtube`: search YouTube or use a YouTube URL, then select an exact result
- `/artists`: browse Artist → Album → Track and queue an exact song
- Artist and album views include Back buttons that preserve the previous page
- `/scan_music`: register audio files placed below `data/music` (DJ/admin only)
- `/play`: request a fresh pCloud URL and stream the first matching track
- `/queue`: browse upcoming songs, select one, and move it up or down
- `/nowplaying`: show queue state and playback control buttons
- `/playlist create|add|remove|play`, `/playlists`: personal and server playlists
- `/history`, `/recent`: personal and server listening history
- `/top songs`, `/top artists`, `/my stats`: listening charts and statistics
- `/favorite`, `/unfavorite`, `/favorites`: manage personal pCloud and YouTube songs
- `/random`, `/randomalbum`: discover a random song or queue a random album
- `/album play`: queue every indexed song in a chosen album, in track order
- `/status`: show uptime, latency, catalogue size, sync state, and FFmpeg health
- `/pause`, `/resume`, `/back`, `/skip`, `/clearqueue`, `/stop`, `/leave`: playback controls
- `/repeat`: choose Off, Repeat current track, or Repeat queue

The database stores stable pCloud link codes and file IDs. Temporary playback
URLs are requested only when a queued track starts, so they do not expire while
waiting. No pCloud audio is permanently downloaded.

Track filenames are cleaned for display, and search ignores spacing,
punctuation, `.mp3` extensions, Myanmar/Arabic digit differences, and common
Unicode composition differences. `/play` still chooses the first title match
for quick use; use `/search` to select an exact track.

The now-playing panel shows artist, album, cover art, source link, requester,
duration, and an elapsed progress bar. Missing pCloud durations are probed once
with FFprobe on first playback and cached in SQLite.

The player keeps a two-second in-memory Opus buffer before Discord playback to
absorb short source-network stalls. Adjust `AUDIO_PREBUFFER_SECONDS` in `.env`
(or set it to `0` to disable buffering).

When browsing an artist's album, select it and use **Play Album** to queue its
entire track list. `/album play` also offers matching album-title suggestions.

Artists use normalized `artists`, `album_artists`, and `track_artists`
relationships. Embedded pCloud artist tags supplement album-title parsing for
compilations and collaborations. Track and artist slash-command inputs provide
autocomplete suggestions.

The bot checks the 100 newest Blogspot posts every `SYNC_INTERVAL_HOURS`
(default: 6). Only new or edited posts have their pCloud folders refreshed.
MediaFire support is not implemented.

YouTube favorites store only video metadata and the original URL. When replayed,
a fresh audio URL is requested. YouTube results are resolved only when playback
starts. Videos are not permanently downloaded. Keep `yt-dlp` updated because
YouTube playback extraction changes regularly.
