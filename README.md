# Daisy MusicBot

Daisy is being rebuilt as a Node.js Discord music bot on top of
[`umutxyp/MusicBot`](https://github.com/umutxyp/MusicBot) / Beatra.

The imported playback baseline is pinned to upstream commit
`e3c825e5ec19c8756bf6612bb7f1f7569501e526` (Beatra v16.0.0).

Use these two files as the starting brief:

- `PROJECT_SUMMARY.md`
- `FUNCTIONALITY_SPEC.md`

Keep and reuse the existing catalogue database:

- `data/daisy_v2/catalog.db`

Do not scrape Phyu Ni War Pyar again from scratch unless the catalogue database
is missing, corrupt, or intentionally reset.

## Local setup

Requirements:

- Node.js 24.11.1 or newer
- A Discord bot token and application client ID
- A development guild ID for fast slash-command registration
- FFmpeg on `PATH` if the optional bundled binary cannot be installed

```powershell
npm install
Copy-Item .env.example .env
npm start
```

The catalogue database is intentionally excluded from Git. Place the retained
database at `data/daisy_v2/catalog.db` or set `CATALOG_DB_PATH` to its location.

## Daisy catalogue commands

- `/phyu play` shows typed track and album suggestions while you type.
- `/phyu search` shows paginated track and album results.
- Selecting a track queues one song; selecting an album queues the full album.
- `/favorites add` searches Phyu tracks with autocomplete and saves the selected song.
- `/favorites remove` autocompletes only from your saved favorites and removes the selection.
- `/favorites list` opens your private paginated favorites picker.
- `/favorites play` autocompletes only from your saved favorites and plays the selection.
- `/nowplaying` opens the same interactive playback card used when a song starts.
- The Queue button opens an in-card paginated queue manager. Select a song and then its new position to reorder it; Clear Queue removes upcoming songs without stopping the current track, and Back returns to playback controls.
- The now-playing card has one Favorite button that toggles the current track on or off for the user who clicks it.

Favorites are stored separately from the read-only catalogue in
`data/daisy_state.db` by default. Set `STATE_DB_PATH` to move this writable
database. Temporary pCloud and audio-stream URLs are never stored.
