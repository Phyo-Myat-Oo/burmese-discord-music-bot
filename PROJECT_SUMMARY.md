# Project Summary: Daisy MusicBot Rebuild

## Goal

Rebuild Daisy as a Node.js Discord music bot based on `umutxyp/MusicBot`
/ Beatra.

Reference repo:

https://github.com/umutxyp/MusicBot

The new bot should use MusicBot's stronger playback foundation and add a custom
Burmese catalogue layer for Phyu Ni War Pyar Blogspot, pCloud, and MediaFire.

## Key Constraint

Do not scrape Phyu Ni War Pyar again from scratch.

Reuse the existing catalogue database first:

- `data/daisy_v2/catalog.db`

The new project should build a catalogue adapter/importer for that SQLite file.
Future scraping should only be incremental:

- new Blogspot posts
- changed Blogspot posts
- failed pCloud folders
- missing or failed MediaFire records

## Why Use MusicBot

MusicBot already provides a better base for the audio side:

- Discord.js structure
- slash commands
- queue/player system
- button controls
- local audio cache
- playback retry/fallback ideas
- YouTube and other music providers
- cookies support for YouTube bot checks

The custom work should focus on the Burmese catalogue, not rebuilding a music
player from nothing.

## Catalogue Features To Preserve

- Blogspot post catalogue from `https://phyuniwarpyar.blogspot.com/`
- pCloud public folder support
- MediaFire fallback support
- stable track identity in the database
- fresh pCloud playback URL resolution only when needed
- Burmese title cleaning
- forgiving search
- artist -> album -> track browsing
- full album playback
- favorites
- playlists
- history and stats
- rich now-playing display

## Important Rules

- Never store expiring pCloud playback/download URLs.
- Store stable pCloud public link code and file ID.
- Store stable MediaFire folder/file identifiers where possible.
- Keep `.env` and `cookies.txt` out of Git.
- Use a bot-only Google account for YouTube cookies if cookies are needed.
- Use one running process per Discord bot token.
- Have a slash-command cleanup strategy to avoid duplicate commands.

## Suggested Architecture

Start from MusicBot and add:

- `PhyuCatalog`
  - reads the existing SQLite catalogue
  - provides search, artist, album, and track lookup

- `PhyuIndexer`
  - incremental Blogspot update checks
  - pCloud scanner for new/failed folders
  - MediaFire scanner for new/missing/failed folders

- `PCloudProvider`
  - resolves fresh pCloud playback URLs from stable database IDs

- `MediaFireProvider`
  - resolves MediaFire fallback playback/download links

- `PhyuText`
  - Burmese title cleaning and search normalization

## First Milestone

1. Clone or fork `umutxyp/MusicBot`.
2. Run it locally.
3. Run it on the VPS.
4. Confirm YouTube playback quality.
5. Add a command that searches `data/daisy_v2/catalog.db`.
6. Queue one catalogue pCloud track through MusicBot's player.
7. Add MediaFire fallback.
8. Add browsing, playlists, and stats after playback is stable.
Do not start by crawling the whole site again.
