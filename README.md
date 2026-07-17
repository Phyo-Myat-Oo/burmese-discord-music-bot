# Daisy Discord Music Bot

Daisy is a Discord music bot built for Burmese music communities. It combines the
indexed **Phyu Ni War Pyar** catalogue with YouTube playback, an interactive
queue, favorites, local audio caching, and Burmese help text.

> Daisy က Phyu Ni War Pyar မှ မြန်မာသီချင်းများနှင့် YouTube သီချင်းများကို
> Discord voice channel ထဲတွင် ရှာဖွေ၊ ရွေးချယ်ပြီး အတူတူနားထောင်နိုင်သည့်
> music bot ဖြစ်ပါတယ်။

The playback foundation is based on
[`umutxyp/MusicBot`](https://github.com/umutxyp/MusicBot) / Beatra, with a custom
catalogue and user experience for Daisy.

## Highlights

- Search Burmese titles, artists, albums, and tracks from a retained SQLite catalogue.
- Browse paginated Phyu search results or use autocomplete for quick playback.
- Queue one track or a complete album with a single selection.
- Resolve fresh pCloud links only when needed, with MediaFire as a fallback.
- Search up to 25 YouTube results across pages of nine.
- Select songs across multiple YouTube pages and queue them together.
- Save personal favorites and play them from autocomplete or a private browser.
- Control playback with Previous, Pause/Resume, Skip, Stop, Shuffle, Volume,
  Repeat, Autoplay, Favorite, and Queue buttons.
- Reorder queued tracks, clear upcoming songs, and return to the Now Playing card.
- Download and convert audio into a local Opus cache before playback.
- Preload upcoming tracks and retain at most ten cached files.
- Restore active playback state after a safe restart.
- Display `/help` in Burmese.

## Commands

| Command | Description |
| --- | --- |
| `/phyu play query:` | Type a Burmese track, artist, or album name and choose an autocomplete suggestion. Selecting an album queues every indexed track. |
| `/phyu search query:` | Open a paginated Phyu catalogue result picker. |
| `/youtube play query:` | Play the first matching YouTube song or a YouTube URL. |
| `/youtube search query:` | Browse paginated YouTube results, retain selections across pages, and add them together. |
| `/favorites add query:` | Save an indexed Phyu track. |
| `/favorites list` | Browse and play saved favorites. |
| `/favorites play query:` | Play a saved favorite using autocomplete. |
| `/favorites remove query:` | Remove a saved favorite. |
| `/nowplaying` | Reopen the current interactive Now Playing card. |
| `/help` | Show the Burmese command and control guide. |

You must be inside a voice channel before starting playback. The bot must be able
to view, connect to, and speak in that channel.

## Requirements

- Node.js **24.11.1 or newer**
- npm
- FFmpeg on `PATH`, or the optional `ffmpeg-static` package
- A Discord application with a bot token and client ID
- `data/daisy_v2/catalog.db`

On Ubuntu:

```bash
sudo apt update
sudo apt install -y ffmpeg git
```

## Discord application setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot**, create/reset the token, and store it only in `.env`.
3. Enable **Server Members Intent** and **Message Content Intent** under Privileged Gateway Intents.
4. In **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes.
5. Grant these bot permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Connect
   - Speak
6. Invite the bot to the server.

Administrator permission is not required.

## Local installation

```powershell
git clone https://github.com/Phyo-Myat-Oo/burmese-discord-music-bot.git
cd burmese-discord-music-bot
git switch codex/daisy-v2
npm install
Copy-Item .env.example .env
```

Edit `.env`:

```dotenv
DISCORD_TOKEN=your_bot_token
CLIENT_ID=your_application_id
GUILD_ID=your_test_server_id

CATALOG_DB_PATH=./data/daisy_v2/catalog.db
STATE_DB_PATH=./data/daisy_state.db

# Optional when YouTube requires authentication
# COOKIES_FILE=./cookies.txt
```

`GUILD_ID` is recommended during development because guild commands update
quickly. Remove it when you want global commands; Discord may take time to show
global command changes.

Start Daisy:

```powershell
npm start
```

Run the tests:

```powershell
npm test
```

## Catalogue and data

The existing catalogue is stored at:

```text
data/daisy_v2/catalog.db
```

Do not crawl the entire Phyu Ni War Pyar site again unless this database is
missing or intentionally reset. Future indexing should be incremental.

Daisy keeps writable personal and playback state separately:

```text
data/daisy_state.db
database/playerState.json
```

Important provider rules:

- Store stable pCloud public-link codes and file IDs—not expiring download URLs.
- Request a fresh pCloud URL when a track is prepared.
- Use the saved MediaFire identifier only when pCloud cannot resolve the track.
- Never commit `.env`, `cookies.txt`, or exported browser credentials.

## YouTube authentication

YouTube may reject requests from VPS addresses with “Sign in to confirm you’re
not a bot.” Daisy supports a Netscape-format cookie file:

```dotenv
COOKIES_FILE=./cookies.txt
```

Copy it securely to the VPS and restrict access:

```bash
chmod 600 /root/daisy-musicbot/cookies.txt
```

Use a dedicated bot-only Google account. Cookies expire and may need to be
exported again. Never add the cookie file to Git.

The `postinstall` script updates the bundled yt-dlp binary after dependency
installation because YouTube extraction changes frequently.

## VPS deployment with systemd

Example location:

```bash
cd /root
git clone -b codex/daisy-v2 https://github.com/Phyo-Myat-Oo/burmese-discord-music-bot.git daisy-musicbot
cd /root/daisy-musicbot
npm ci
nano .env
```

Create `/etc/systemd/system/daisy-musicbot.service`:

```ini
[Unit]
Description=Daisy Discord Music Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/daisy-musicbot
ExecStart=/usr/bin/node /root/daisy-musicbot/index.js
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now daisy-musicbot
sudo systemctl status daisy-musicbot
journalctl -u daisy-musicbot -f
```

Deploy an update:

```bash
cd /root/daisy-musicbot
git pull origin codex/daisy-v2
npm ci
sudo systemctl restart daisy-musicbot
```

Only one Daisy process should use a Discord bot token at a time:

```bash
pgrep -af 'node|npm'
systemctl show daisy-musicbot --property=MainPID
```

## Troubleshooting

### YouTube does not play

- Update dependencies with `npm ci` so the yt-dlp postinstall update runs.
- Confirm `COOKIES_FILE` points to an existing readable cookie file.
- Test the cookie file without printing its contents.
- See [`YOUTUBE_FIX.md`](YOUTUBE_FIX.md) for additional checks.

### Commands appear twice

Confirm that only one bot application/token is online. Old global commands can
also remain visible briefly after switching to guild command registration.

### A pCloud track cannot play

The public link may have reached its traffic limit. Daisy will try a saved
MediaFire fallback when one is available.

### Audio cache grows

Cached audio is stored in `audio_cache/`, which is excluded from Git. Daisy
protects the current and upcoming tracks while keeping a maximum of ten cached
files during normal operation.

## Security

- Never commit bot tokens, cookie files, personal access tokens, or `.env`.
- Rotate the Discord token immediately if it is exposed.
- Prefer a repository-scoped deploy key on a shared VPS.
- Run only one process for each bot token.
- Back up the catalogue before performing migrations or incremental indexing.

## Attribution

Daisy uses the playback architecture from
[`umutxyp/MusicBot`](https://github.com/umutxyp/MusicBot), imported from upstream
commit `e3c825e5ec19c8756bf6612bb7f1f7569501e526` (Beatra v16.0.0), and extends it
with the Phyu Ni War Pyar catalogue, pCloud/MediaFire resolution, Burmese search,
favorites, and Daisy-specific Discord interfaces.

## License

See [`LICENSE`](LICENSE).
