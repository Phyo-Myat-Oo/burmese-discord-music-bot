# Raspberry Pi Self-Hosting & Deployment Guide

This guide documents how to set up and deploy the **Daisy Discord Music Bot** to run 24/7 on a Raspberry Pi using `systemd`.

---

## 1. System Requirements & Dependencies

Before setting up the project, install the necessary system dependencies on the Raspberry Pi:

```bash
# Update package repositories
sudo apt update

# Install Python tools, FFmpeg, Opus (for audio streaming), and compilation tools
sudo apt install python3-venv python3-pip ffmpeg libopus-dev build-essential unzip -y
```

---

## 2. Transferring & Extracting Data

To copy the existing catalogue database (`data.zip`) from your local Mac to the Raspberry Pi:

1. On your **Mac terminal** (outside SSH), run:
   ```bash
   scp /Users/tklwin/GithubRepos/burmese-discord-music-bot/data.zip rpi@rpi.local:/home/rpi/burmese-discord-music-bot/
   ```

2. On your **Raspberry Pi terminal**, unzip the file in the project folder:
   ```bash
   cd ~/burmese-discord-music-bot
   unzip data.zip
   ```
   *This extracts the SQLite database directly into `data/music.db`.*

---

## 3. Python Virtual Environment & Requirements

1. Create a virtual environment inside the repository directory:
   ```bash
   python3 -m venv .venv
   ```

2. Activate the virtual environment:
   ```bash
   source .venv/bin/activate
   ```

3. Upgrade pip and install all Python requirements:
   ```bash
   pip install --upgrade pip
   ```
   ```bash
   pip install -r requirements.txt
   ```

---

## 4. Configuration

1. Create the environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your editor (e.g., `nano`):
   ```bash
   nano .env
   ```
   Ensure the following are set:
   * `DISCORD_TOKEN`: Your Discord bot client token.
   * `DISCORD_GUILD_ID`: (Optional) Your Discord Server ID for instant slash command synchronization.
   * `FFMPEG_PATH`: Leave blank (system-wide `ffmpeg` will be auto-discovered).

---

## 5. Setting up the 24/7 Background Service (Systemd)

To make sure the bot runs continuously, starts on boot, and automatically restarts on crash, configure a systemd service.

1. Create a service file:
   ```bash
   sudo nano /etc/systemd/system/discord-music-bot.service
   ```

2. Paste the following configuration:
   ```ini
   [Unit]
   Description=Daisy Discord Music Bot
   After=network.target

   [Service]
   Type=simple
   User=rpi
   WorkingDirectory=/home/rpi/burmese-discord-music-bot
   ExecStart=/home/rpi/burmese-discord-music-bot/.venv/bin/python bot.py
   Restart=on-failure
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. Reload systemd, start the service, and enable it on boot:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl start discord-music-bot
   sudo systemctl enable discord-music-bot
   ```

---

## 6. Managing the Service & Logs

Use these standard commands to control and inspect the bot:

| Command | Action |
| --- | --- |
| `sudo systemctl status discord-music-bot` | Check if the bot is running, stopped, or has crashed. |
| `sudo journalctl -u discord-music-bot -f -n 50` | Stream real-time logs and printed output from the bot. |
| `sudo systemctl restart discord-music-bot` | Restart the bot (required after modifying `.env` or files). |
| `sudo systemctl stop discord-music-bot` | Stop the bot. |
| `sudo systemctl start discord-music-bot` | Start the bot if it is stopped. |
