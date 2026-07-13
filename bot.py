from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
import aiohttp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from music.library import Library
from music.pcloud import PCloudClient
from music.player import GuildPlayer, find_ffmpeg
from music.scraper import SiteScraper

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"]) if os.getenv("DISCORD_GUILD_ID") else None
ADMIN_ROLE = os.getenv("MUSIC_ADMIN_ROLE", "DJ")
SYNC_INTERVAL_HOURS = float(os.getenv("SYNC_INTERVAL_HOURS", "6"))


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.library = Library(Path("data/music.db"), Path("data/music"))
        self.scraper = SiteScraper(self.library)
        self.players: dict[int, GuildPlayer] = {}
        self.sync_lock = asyncio.Lock()
        self.auto_sync_task: asyncio.Task | None = None
        self.started_at = datetime.now(timezone.utc)
        self.last_sync_at: datetime | None = None
        self.last_sync_result: dict[str, int] | None = None

    async def setup_hook(self) -> None:
        await self.library.initialize()
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if SYNC_INTERVAL_HOURS > 0:
            self.auto_sync_task = asyncio.create_task(self._automatic_sync())

    def player(self, guild: discord.Guild) -> GuildPlayer:
        return self.players.setdefault(guild.id, GuildPlayer(guild))

    async def incremental_sync(self) -> dict[str, int]:
        async with self.sync_lock:
            result = await self.scraper.sync_posts(max_posts=100, incremental=True)
            self.last_sync_at = datetime.now(timezone.utc)
            self.last_sync_result = result
            return result

    async def _automatic_sync(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(SYNC_INTERVAL_HOURS * 3600)
            try:
                result = await self.incremental_sync()
                logging.info(
                    "Automatic catalogue sync: %s posts, %s albums, %s tracks, %s failures",
                    result["posts"], result["albums"], result["tracks"], result["failed"],
                )
            except Exception:
                logging.exception("Automatic catalogue sync failed")

    async def close(self) -> None:
        if self.auto_sync_task:
            self.auto_sync_task.cancel()
        await asyncio.gather(*(player.close() for player in self.players.values()), return_exceptions=True)
        await super().close()


bot = MusicBot()


def is_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return isinstance(member, discord.Member) and (
        member.guild_permissions.manage_guild or any(role.name == ADMIN_ROLE for role in member.roles)
    )


async def queue_pcloud_track(interaction: discord.Interaction, track) -> str:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
        raise ValueError("Join a voice channel first.")
    if not interaction.guild:
        raise ValueError("Music playback is only available in a server.")

    player = bot.player(interaction.guild)
    await player.connect(member.voice.channel)

    async def resolve() -> str:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            return await PCloudClient(session).stream_url(track["code"], track["file_id"])

    position = await player.enqueue_stream(
        resolve, track["title"], track["album_title"], member.display_name, track["id"]
    )
    return (
        f"Queued **{track['title']}**\nAlbum: {track['album_title']}\n"
        f"Queue position: {position}"
    )


class TrackSelect(discord.ui.Select):
    def __init__(self, rows) -> None:
        options = [
            discord.SelectOption(
                label=row["title"][:100],
                description=row["album_title"][:100],
                value=str(row["id"]),
            )
            for row in rows
        ]
        super().__init__(placeholder="Choose the exact song to play", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TrackSearchView = self.view  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True, thinking=True)
        track = await bot.library.pcloud_track_by_id(int(self.values[0]))
        if not track:
            await interaction.followup.send("That track no longer exists.", ephemeral=True)
            return
        try:
            message = await queue_pcloud_track(interaction, track)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(message, ephemeral=True)


class TrackSearchView(discord.ui.View):
    PAGE_SIZE = 10

    def __init__(self, query: str, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.query = query
        self.owner_id = owner_id
        self.page = 0
        self.total = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run your own `/search` to choose a song.", ephemeral=True)
        return False

    async def render(self) -> discord.Embed:
        self.total = await bot.library.count_pcloud_tracks(self.query)
        pages = max(1, math.ceil(self.total / self.PAGE_SIZE))
        self.page = min(max(self.page, 0), pages - 1)
        rows = await bot.library.search_pcloud_tracks(
            self.query, limit=self.PAGE_SIZE, offset=self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, TrackSelect):
                self.remove_item(child)
        if rows:
            self.add_item(TrackSelect(rows))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        lines = [
            f"**{self.page * self.PAGE_SIZE + index}. {row['title']}**\n{row['album_title']}"
            for index, row in enumerate(rows, start=1)
        ]
        embed = discord.Embed(
            title=f"Search: {self.query}",
            description="\n\n".join(lines) or "No matching tracks.",
            color=discord.Color.teal(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} matching tracks")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)


class ArtistSelect(discord.ui.Select):
    def __init__(self, rows) -> None:
        options = [
            discord.SelectOption(
                label=row["artist"][:100],
                description=f"{row['album_count']:,} albums",
                value=row["artist"][:100],
            )
            for row in rows
        ]
        super().__init__(placeholder="Choose an artist", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        parent: ArtistBrowserView = self.view  # type: ignore[assignment]
        album_view = AlbumBrowserView(
            self.values[0], parent.owner_id, parent.filter_text, parent.page
        )
        embed = await album_view.render()
        await interaction.response.edit_message(embed=embed, view=album_view)


class ArtistBrowserView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, owner_id: int, filter_text: str = "") -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.filter_text = filter_text.strip()
        self.page = 0
        self.total = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run your own `/artists` browser.", ephemeral=True)
        return False

    async def render(self) -> discord.Embed:
        self.total = await bot.library.count_artists(self.filter_text)
        pages = max(1, math.ceil(self.total / self.PAGE_SIZE))
        self.page = min(max(self.page, 0), pages - 1)
        rows = await bot.library.list_artists(
            self.filter_text, limit=self.PAGE_SIZE, offset=self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, ArtistSelect):
                self.remove_item(child)
        if rows:
            self.add_item(ArtistSelect(rows))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        lines = [
            f"**{self.page * self.PAGE_SIZE + index}. {row['artist']}** — {row['album_count']:,} albums"
            for index, row in enumerate(rows, start=1)
        ]
        title = "Browse artists" if not self.filter_text else f"Artists matching: {self.filter_text}"
        embed = discord.Embed(
            title=title,
            description="\n".join(lines) or "No matching artists.",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} artists")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)


class AlbumSelect(discord.ui.Select):
    def __init__(self, rows) -> None:
        options = [
            discord.SelectOption(
                label=row["title"][:100],
                description=f"{row['track_count']:,} tracks",
                value=str(row["id"]),
            )
            for row in rows
        ]
        super().__init__(placeholder="Choose an album", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        parent: AlbumBrowserView = self.view  # type: ignore[assignment]
        album = await bot.library.album_by_id(int(self.values[0]))
        if not album:
            await interaction.response.send_message("That album no longer exists.", ephemeral=True)
            return
        track_view = AlbumTrackView(
            album["id"], album["title"], parent.artist, parent.owner_id,
            parent.page, parent.artist_filter, parent.artist_page,
        )
        embed = await track_view.render()
        await interaction.response.edit_message(embed=embed, view=track_view)


class AlbumBrowserView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(
        self, artist: str, owner_id: int,
        artist_filter: str = "", artist_page: int = 0,
    ) -> None:
        super().__init__(timeout=300)
        self.artist = artist
        self.owner_id = owner_id
        self.page = 0
        self.total = 0
        self.artist_filter = artist_filter
        self.artist_page = artist_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run your own `/artists` browser.", ephemeral=True)
        return False

    async def render(self) -> discord.Embed:
        self.total = await bot.library.count_albums_by_artist(self.artist)
        pages = max(1, math.ceil(self.total / self.PAGE_SIZE))
        self.page = min(max(self.page, 0), pages - 1)
        rows = await bot.library.list_albums_by_artist(
            self.artist, self.PAGE_SIZE, self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, AlbumSelect):
                self.remove_item(child)
        if rows:
            self.add_item(AlbumSelect(rows))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        lines = [
            f"**{self.page * self.PAGE_SIZE + i}. {row['title']}** — {row['track_count']:,} tracks"
            for i, row in enumerate(rows, 1)
        ]
        embed = discord.Embed(
            title=f"Albums by {self.artist}",
            description="\n".join(lines) or "No albums found.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} albums")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Back to Artists", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = ArtistBrowserView(self.owner_id, self.artist_filter)
        view.page = self.artist_page
        await interaction.response.edit_message(embed=await view.render(), view=view)


class AlbumTrackView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(
        self, album_id: int, album_title: str, artist: str,
        owner_id: int, album_page: int = 0,
        artist_filter: str = "", artist_page: int = 0,
    ) -> None:
        super().__init__(timeout=300)
        self.album_id = album_id
        self.album_title = album_title
        self.owner_id = owner_id
        self.artist = artist
        self.album_page = album_page
        self.artist_filter = artist_filter
        self.artist_page = artist_page
        self.page = 0
        self.total = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run your own `/artists` browser.", ephemeral=True)
        return False

    async def render(self) -> discord.Embed:
        self.total = await bot.library.count_album_tracks(self.album_id)
        pages = max(1, math.ceil(self.total / self.PAGE_SIZE))
        self.page = min(max(self.page, 0), pages - 1)
        rows = await bot.library.list_album_tracks(
            self.album_id, self.PAGE_SIZE, self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, TrackSelect):
                self.remove_item(child)
        if rows:
            self.add_item(TrackSelect(rows))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        lines = [
            f"**{self.page * self.PAGE_SIZE + i}. {row['title']}**"
            for i, row in enumerate(rows, 1)
        ]
        embed = discord.Embed(
            title=self.album_title,
            description="\n".join(lines) or "No tracks found.",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} tracks")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Back to Albums", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = AlbumBrowserView(
            self.artist, self.owner_id, self.artist_filter, self.artist_page
        )
        view.page = self.album_page
        await interaction.response.edit_message(embed=await view.render(), view=view)


class FavoriteTracksView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.page = 0
        self.total = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Open your own `/favorites` list.", ephemeral=True)
        return False

    async def render(self) -> discord.Embed:
        self.total = await bot.library.count_favorites(self.owner_id)
        pages = max(1, math.ceil(self.total / self.PAGE_SIZE))
        self.page = min(max(self.page, 0), pages - 1)
        rows = await bot.library.list_favorites(
            self.owner_id, self.PAGE_SIZE, self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, TrackSelect):
                self.remove_item(child)
        if rows:
            self.add_item(TrackSelect(rows))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        lines = [
            f"**{self.page * self.PAGE_SIZE + i}. {row['title']}**\n{row['album_title']}"
            for i, row in enumerate(rows, 1)
        ]
        embed = discord.Embed(
            title="Your favorite songs",
            description="\n\n".join(lines) or "You have no favorites yet.",
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} favorites")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)


def now_playing_embed(player: GuildPlayer) -> discord.Embed:
    item = player.current
    if not item:
        return discord.Embed(description="Nothing is playing.", color=discord.Color.dark_grey())
    status = "Paused" if player.is_paused else "Playing"
    embed = discord.Embed(title=f"{status}: {item.title}", color=discord.Color.purple())
    embed.add_field(name="Album", value=item.album or "Unknown", inline=False)
    embed.add_field(name="Requested by", value=item.requester, inline=True)
    embed.add_field(name="Up next", value=str(player.queue.qsize()), inline=True)
    return embed


class PlayerControlsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        player = bot.player(interaction.guild)
        if not interaction.user.voice or not player.voice or interaction.user.voice.channel != player.voice.channel:
            await interaction.response.send_message(
                "Join Daisy's voice channel to use the controls.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        player.pause()
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)

    @discord.ui.button(label="Resume", emoji="▶️", style=discord.ButtonStyle.success)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        player.resume()
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        player.skip()
        await interaction.response.send_message("Skipped.", ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        player.stop()
        await asyncio.sleep(0.1)
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)

    @discord.ui.button(label="Favorite", emoji="❤️", style=discord.ButtonStyle.secondary)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        if not player.current or player.current.track_id is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        added = await bot.library.add_favorite(interaction.user.id, player.current.track_id)
        await interaction.response.send_message(
            "Added to your favorites." if added else "That song is already a favorite.",
            ephemeral=True,
        )


@bot.tree.command(description="Index new albums from Phyu Ni War Pyar")
async def sync(interaction: discord.Interaction) -> None:
    if not is_admin(interaction):
        await interaction.response.send_message(f"The `{ADMIN_ROLE}` role is required.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await bot.incremental_sync()
    await interaction.followup.send(
        f"Indexed {result['posts']} posts and {result['tracks']} pCloud tracks; "
        f"{result['failed']} links failed.", ephemeral=True
    )


@bot.tree.command(description="Search indexed Burmese music posts")
@app_commands.describe(query="Artist, album, or song name")
async def search(interaction: discord.Interaction, query: str) -> None:
    view = TrackSearchView(query.strip(), interaction.user.id)
    embed = await view.render()
    if not view.total:
        await interaction.response.send_message("No matching indexed tracks. Ask a DJ to run `/sync`.", ephemeral=True)
        return
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(description="Browse artists, then choose a song")
@app_commands.describe(filter_text="Optional part of an artist name")
async def artists(interaction: discord.Interaction, filter_text: str = "") -> None:
    view = ArtistBrowserView(interaction.user.id, filter_text)
    embed = await view.render()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(description="Register audio files placed in data/music")
async def scan_music(interaction: discord.Interaction) -> None:
    if not is_admin(interaction):
        await interaction.response.send_message(f"The `{ADMIN_ROLE}` role is required.", ephemeral=True)
        return
    count = await bot.library.scan_local_tracks()
    await interaction.response.send_message(f"Registered {count} local audio files.", ephemeral=True)


@bot.tree.command(description="Stream an indexed pCloud song by title")
@app_commands.describe(query="Song filename or album name")
async def play(interaction: discord.Interaction, query: str) -> None:
    track = await bot.library.find_pcloud_track(query)
    if not track:
        await interaction.response.send_message("That pCloud track is not indexed yet.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        message = await queue_pcloud_track(interaction, track)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(message)


def selected_or_current_track(interaction: discord.Interaction, query: str = ""):
    async def resolve():
        if query.strip():
            return await bot.library.find_pcloud_track(query.strip())
        if interaction.guild:
            current = bot.player(interaction.guild).current
            if current and current.track_id is not None:
                return await bot.library.pcloud_track_by_id(current.track_id)
        return None
    return resolve()


@bot.tree.command(description="Add a song or the current song to your favorites")
@app_commands.describe(query="Optional song name; omit to favorite the current song")
async def favorite(interaction: discord.Interaction, query: str = "") -> None:
    track = await selected_or_current_track(interaction, query)
    if not track:
        await interaction.response.send_message("No matching or currently playing track.", ephemeral=True)
        return
    added = await bot.library.add_favorite(interaction.user.id, track["id"])
    await interaction.response.send_message(
        f"Added **{track['title']}** to your favorites." if added
        else f"**{track['title']}** is already a favorite.",
        ephemeral=True,
    )


@bot.tree.command(description="Remove a song or the current song from your favorites")
@app_commands.describe(query="Optional song name; omit to remove the current song")
async def unfavorite(interaction: discord.Interaction, query: str = "") -> None:
    track = await selected_or_current_track(interaction, query)
    if not track:
        await interaction.response.send_message("No matching or currently playing track.", ephemeral=True)
        return
    removed = await bot.library.remove_favorite(interaction.user.id, track["id"])
    await interaction.response.send_message(
        f"Removed **{track['title']}** from your favorites." if removed
        else "That song was not in your favorites.",
        ephemeral=True,
    )


@bot.tree.command(description="Browse and play your favorite songs")
async def favorites(interaction: discord.Interaction) -> None:
    view = FavoriteTracksView(interaction.user.id)
    await interaction.response.send_message(embed=await view.render(), view=view, ephemeral=True)


@bot.tree.command(description="Queue a random song, optionally from an artist")
@app_commands.describe(artist="Optional artist name")
async def random(interaction: discord.Interaction, artist: str = "") -> None:
    track = await bot.library.random_track(artist.strip())
    if not track:
        await interaction.response.send_message("No matching tracks found.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        message = await queue_pcloud_track(interaction, track)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(f"🎲 {message}")


@bot.tree.command(description="Queue a random album, optionally from an artist")
@app_commands.describe(artist="Optional artist name")
async def randomalbum(interaction: discord.Interaction, artist: str = "") -> None:
    album = await bot.library.random_album(artist.strip())
    if not album:
        await interaction.response.send_message("No matching albums found.", ephemeral=True)
        return
    tracks = await bot.library.album_playback_tracks(album["id"], limit=50)
    await interaction.response.defer(thinking=True)
    try:
        for track in tracks:
            await queue_pcloud_track(interaction, track)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(
        f"🎲 Queued **{album['title']}** with {len(tracks)} tracks."
    )


@bot.tree.command(name="queue", description="Show the current song and upcoming queue")
async def show_queue(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    player = bot.player(interaction.guild)
    lines = []
    if player.current:
        lines.append(f"**Now:** {player.current.title} — requested by {player.current.requester}")
    for index, item in enumerate(player.upcoming(10), 1):
        lines.append(f"**{index}.** {item.title} — {item.requester}")
    if player.queue.qsize() > 10:
        lines.append(f"…and {player.queue.qsize() - 10} more")
    embed = discord.Embed(
        title="Music queue",
        description="\n".join(lines) or "The queue is empty.",
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=PlayerControlsView())


@bot.tree.command(description="Show the current song and playback controls")
async def nowplaying(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    player = bot.player(interaction.guild)
    await interaction.response.send_message(embed=now_playing_embed(player), view=PlayerControlsView())


@bot.tree.command(description="Show bot health and catalogue information")
async def status(interaction: discord.Interaction) -> None:
    stats = await bot.library.catalogue_stats()
    elapsed = datetime.now(timezone.utc) - bot.started_at
    days, remainder = divmod(int(elapsed.total_seconds()), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    active_players = sum(
        1 for player in bot.players.values() if player.voice and player.voice.is_connected()
    )
    last_sync = (
        discord.utils.format_dt(bot.last_sync_at, style="R") if bot.last_sync_at else "Not since startup"
    )
    sync_detail = ""
    if bot.last_sync_result:
        sync_detail = (
            f"\nLast result: {bot.last_sync_result['albums']} albums, "
            f"{bot.last_sync_result['tracks']} tracks"
        )
    database_mb = bot.library.database.stat().st_size / (1024 * 1024)
    embed = discord.Embed(title="Daisy status", color=discord.Color.green())
    embed.add_field(name="Uptime", value=f"{days}d {hours}h {minutes}m")
    embed.add_field(name="Discord latency", value=f"{bot.latency * 1000:.0f} ms")
    embed.add_field(name="Voice players", value=str(active_players))
    embed.add_field(name="Posts", value=f"{stats['posts']:,}")
    embed.add_field(name="Albums", value=f"{stats['albums']:,}")
    embed.add_field(name="Tracks", value=f"{stats['tracks']:,}")
    embed.add_field(name="Favorites", value=f"{stats['favorites']:,}")
    embed.add_field(name="Broken links", value=f"{stats['failed']:,}")
    embed.add_field(name="Database", value=f"{database_mb:.1f} MB")
    embed.add_field(name="Last catalogue sync", value=last_sync + sync_detail, inline=False)
    embed.set_footer(text=f"FFmpeg: {Path(find_ffmpeg()).name} • Auto-sync every {SYNC_INTERVAL_HOURS:g}h")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(description="Pause playback")
async def pause(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    changed = bot.player(interaction.guild).pause()
    await interaction.response.send_message("Paused." if changed else "Nothing is playing.")


@bot.tree.command(description="Resume paused playback")
async def resume(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    changed = bot.player(interaction.guild).resume()
    await interaction.response.send_message("Resumed." if changed else "Nothing is paused.")


@bot.tree.command(description="Skip the current track")
async def skip(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    bot.player(interaction.guild).skip()
    await interaction.response.send_message("Skipped.")


@bot.tree.command(description="Stop playback and clear the queue")
async def stop(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    bot.player(interaction.guild).stop()
    await interaction.response.send_message("Stopped playback and cleared the queue.")


@bot.tree.command(description="Stop playback and leave voice")
async def leave(interaction: discord.Interaction) -> None:
    if interaction.guild:
        await bot.player(interaction.guild).close()
    await interaction.response.send_message("Disconnected.")


@bot.event
async def on_ready() -> None:
    logging.info("Logged in as %s", bot.user)


if __name__ == "__main__":
    if not TOKEN or TOKEN == "paste_your_bot_token_here":
        raise SystemExit("Set DISCORD_TOKEN in .env first.")
    bot.run(TOKEN)
