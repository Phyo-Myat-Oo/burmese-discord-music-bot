# Functionality Spec: Daisy On MusicBot

## Product Vision

Daisy is a Discord music bot for a Burmese music server.

It should feel like a polished general music bot, while also acting as a custom
Burmese archive player for Phyu Ni War Pyar Blogspot albums hosted on pCloud and
MediaFire.

Base project:

https://github.com/umutxyp/MusicBot

## Existing Catalogue

Use this existing SQLite catalogue first:

- `data/daisy_v2/catalog.db`

Do not scrape Phyu Ni War Pyar from scratch unless the database is missing,
corrupt, or intentionally reset.

The first custom feature should be a Node.js catalogue adapter that can read
this database and expose:

- track search
- artist search
- album lookup
- album tracks
- random track
- random album
- pCloud identifiers
- MediaFire identifiers
- Blogspot source links
- cover art

## Core Playback

Use MusicBot's player/queue foundation where possible.

Required playback sources:

- YouTube
- pCloud catalogue tracks
- MediaFire fallback tracks

Queue controls:

- play
- pause
- resume
- skip
- back
- stop
- leave
- clear queue
- move up/down
- repeat track
- repeat queue
- queue full album

The bot should cache/download in the style MusicBot already supports, rather
than streaming unstable source URLs directly when a safer cache path exists.

## pCloud Rules

Store stable values only:

- public link code
- file ID
- file name
- size
- duration if available
- album/post relationship

Do not store URLs returned by pCloud `getpublinkdownload`. They expire.

At playback time:

1. read `pcloud_code` and `pcloud_file_id`
2. request a fresh pCloud playback/download URL
3. pass it into MusicBot's playback/cache pipeline

If pCloud is traffic-limited or unavailable, try MediaFire fallback.

## MediaFire Rules

Store stable values where available:

- folder URL
- folder key
- file quickkey
- file name
- size
- album/post relationship

MediaFire should support:

- fallback for pCloud tracks
- MediaFire-only albums
- retry when folder/file lookup previously failed

## Search And Burmese Text

Search should be forgiving.

Normalize:

- `.mp3`
- track numbers like `01.`
- punctuation
- repeated spaces
- Myanmar digits and Arabic digits
- Unicode composition differences
- artist/album separators

Search should match:

- track title
- artist
- album
- Blogspot post title

Display should show:

- clean title
- artist
- album
- cover art where available

## Browsing UX

Artist browsing:

- list artists
- filter artists
- select artist
- view albums
- back button

Album browsing:

- list albums for artist
- show track list
- play full album
- select exact track
- back button

Track search:

- paginated results
- dropdown/select menu
- exact track play

Random:

- random track
- random artist track
- random album

## Now Playing UI

Now-playing card should include:

- cover image or YouTube thumbnail
- title
- artist
- album
- source link
- requester
- duration
- progress
- queue count
- repeat mode

Buttons:

- pause/resume
- skip
- back
- stop
- queue
- clear queue
- repeat

## Favorites

Support:

- favorite current track
- unfavorite
- list favorites
- play favorite

Favorite entries should store:

- user ID
- source type
- stable source key
- title
- artist
- album
- source URL
- duration
- thumbnail/cover

YouTube favorites must store the video ID/URL, not a temporary audio URL.

## Playlists

Support:

- create playlist
- add current/search result
- remove item
- play playlist
- list playlists

Scopes:

- personal
- server

## History And Stats

Record:

- guild ID
- requester/user ID
- source type
- source key
- title
- artist
- album
- duration
- completed/skipped
- played timestamp

Commands/features:

- recent plays
- personal history
- top songs
- top artists
- user stats

## Admin Features

Admin/DJ-only:

- sync recent posts
- retry failed catalogue items
- status
- optional clear slash commands

Status should show:

- uptime
- latency
- current voice channel
- queue length
- cache size
- catalogue counts
- last sync time
- last errors

## Incremental Indexer

Only after catalogue read/playback works, add an incremental indexer.

It should:

- check new Blogspot posts
- detect changed posts
- scan new pCloud folders
- scan missing/failed MediaFire folders
- store failures
- retry failures later
- print progress
- resume safely after interruption

Do not rebuild the whole catalogue unless explicitly requested.

## Configuration

Expected environment variables:

- `DISCORD_TOKEN`
- `CLIENT_ID`
- `GUILD_ID`
- `MUSIC_ADMIN_ROLE`
- `CATALOG_DB_PATH`
- `STATE_DB_PATH`
- `CACHE_DIR`
- `PHYU_BASE_URL`
- `SYNC_INTERVAL_HOURS`
- `COOKIES_FILE`
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`

Never commit:

- `.env`
- `cookies.txt`
- real Discord tokens
- Google/YouTube cookies

## VPS Deployment

Target:

- Ubuntu 24.04
- systemd service
- one bot process per Discord token

Expected flow:

```bash
npm install
npm run start
```

Systemd should restart the bot on failure and start it after reboot.

## Rebuild Phases

Phase 1:

- run MusicBot locally and on VPS
- verify YouTube playback quality

Phase 2:

- read `data/daisy_v2/catalog.db`
- add catalogue search command
- queue one pCloud track

Phase 3:

- add pCloud fresh URL resolver
- add MediaFire fallback resolver
- add album playback

Phase 4:

- artist/album/track browsing
- now-playing card
- queue controls
- favorites
- playlists

Phase 5:

- history/stats
- incremental indexer
- status/admin tools
- deployment docs

## Acceptance Criteria

Minimum:

- bot runs on VPS
- YouTube plays reliably
- existing catalogue DB is readable
- pCloud catalogue track plays
- MediaFire fallback works
- full album can be queued

Good:

- artist/album browsing works
- queue UI is easy to use
- favorites/playlists work
- now-playing display is rich

Excellent:

- playback is stable on the VPS
- catalogue updates incrementally
- a friend can host from GitHub with only `.env` plus the catalogue database
