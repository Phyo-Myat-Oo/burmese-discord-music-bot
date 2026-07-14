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
from music.youtube import YouTubeClient, YouTubeError, YouTubeResult, is_youtube_url

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"]) if os.getenv("DISCORD_GUILD_ID") else None
ADMIN_ROLE = os.getenv("MUSIC_ADMIN_ROLE", "DJ")
SYNC_INTERVAL_HOURS = float(os.getenv("SYNC_INTERVAL_HOURS", "6"))


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
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
        return self.players.setdefault(
            guild.id, GuildPlayer(
                guild, self._track_started, self._track_finished, self._track_duration
            )
        )

    async def _track_started(self, item) -> int:
        return await self.library.record_play_start(
            self._guild_for_item(item), item.requester_id or 0,
            {
                "source_type": item.source_type, "track_id": item.track_id,
                "source_url": item.source_url, "title": item.title,
                "artist": item.artist or item.uploader, "album": item.album,
                "duration": item.duration,
            },
        )

    def _guild_for_item(self, item) -> int:
        for guild_id, player in self.players.items():
            if player.current is item:
                return guild_id
        return 0

    async def _track_finished(self, item, completed: bool) -> None:
        if completed and item.history_id:
            await self.library.complete_history(item.history_id)

    async def _track_duration(self, item) -> None:
        if item.track_id and item.duration:
            await self.library.update_track_duration(item.track_id, item.duration)

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


def requester_voice_channel(
    interaction: discord.Interaction,
) -> discord.VoiceChannel | discord.StageChannel | None:
    """Find the command caller's channel from the authoritative guild state."""
    if not interaction.guild:
        return None
    state = interaction.guild.voice_states.get(interaction.user.id)
    if state and isinstance(state.channel, (discord.VoiceChannel, discord.StageChannel)):
        return state.channel
    member = interaction.guild.get_member(interaction.user.id)
    if member and member.voice and isinstance(member.voice.channel, (discord.VoiceChannel, discord.StageChannel)):
        return member.voice.channel
    return None


async def queue_pcloud_track(interaction: discord.Interaction, track) -> str:
    if not interaction.guild:
        raise ValueError("Music playback is only available in a server.")
    channel = requester_voice_channel(interaction)
    if channel is None:
        raise ValueError("Discord cannot see you in a voice channel yet. Leave and rejoin it, then try again.")

    player = bot.player(interaction.guild)
    await player.connect(channel)

    async def resolve() -> str:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            return await PCloudClient(session).stream_url(track["code"], track["file_id"])

    position = await player.enqueue_stream(
        resolve, track["title"], track["album_title"], interaction.user.display_name, track["id"],
        source_url=track["post_url"], duration=track["duration"],
        artist=track["artist"] or "Unknown", cover_url=track["cover_url"],
        requester_id=interaction.user.id,
    )
    return (
        f"Queued **{track['title']}**\nAlbum: {track['album_title']}\n"
        f"Queue position: {position}"
    )


async def queue_pcloud_album(interaction: discord.Interaction, album) -> int:
    """Queue every indexed track in an album, preserving pCloud file order."""
    tracks = await bot.library.album_playback_tracks(album["id"])
    if not tracks:
        raise ValueError("That album has no playable tracks.")
    for track in tracks:
        await queue_pcloud_track(interaction, track)
    return len(tracks)


async def queue_youtube_track(interaction: discord.Interaction, result: YouTubeResult) -> str:
    if not interaction.guild:
        raise ValueError("Music playback is only available in a server.")
    channel = requester_voice_channel(interaction)
    if channel is None:
        raise ValueError("Discord cannot see you in a voice channel yet. Leave and rejoin it, then try again.")
    player = bot.player(interaction.guild)
    await player.connect(channel)

    async def resolve() -> dict[str, str | dict]:
        return await YouTubeClient.stream_url(result.url)

    position = await player.enqueue_stream(
        resolve, result.title, f"YouTube • {result.uploader}", interaction.user.display_name,
        source_type="youtube", source_url=result.url, uploader=result.uploader,
        duration=result.duration, thumbnail=result.thumbnail,
        artist=result.uploader, cover_url=result.thumbnail, requester_id=interaction.user.id,
    )
    return (
        f"Queued **{result.title}**\nChannel: {result.uploader}\n"
        f"Duration: {result.duration_text}\nQueue position: {position}"
    )


class YouTubeSelect(discord.ui.Select):
    def __init__(self, results: list[YouTubeResult]) -> None:
        options = [
            discord.SelectOption(
                label=result.title[:100],
                description=f"{result.uploader} • {result.duration_text}"[:100],
                value=result.url,
            )
            for result in results
        ]
        super().__init__(placeholder="Choose a YouTube song", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: YouTubeSearchView = self.view  # type: ignore[assignment]
        result = next((item for item in view.results if item.url == self.values[0]), None)
        if not result:
            await interaction.response.send_message("That result expired. Search again.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await queue_youtube_track(interaction, result)
        except (ValueError, YouTubeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(message, ephemeral=True)


class YouTubeSearchView(discord.ui.View):
    def __init__(self, results: list[YouTubeResult], owner_id: int) -> None:
        super().__init__(timeout=300)
        self.results = results
        self.owner_id = owner_id
        self.add_item(YouTubeSelect(results))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run your own `/youtube` search.", ephemeral=True)
        return False

    def embed(self, query: str) -> discord.Embed:
        lines = [
            f"**{index}. [{result.title}]({result.url})**\n"
            f"{result.uploader} • {result.duration_text}"
            for index, result in enumerate(self.results, 1)
        ]
        embed = discord.Embed(
            title=f"YouTube: {query}"[:256],
            description="\n\n".join(lines),
            color=discord.Color.red(),
        )
        if self.results[0].thumbnail:
            embed.set_thumbnail(url=self.results[0].thumbnail)
        embed.set_footer(text="Select a result below • Streams without permanent download")
        return embed


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

    @discord.ui.button(label="Play Album", emoji="▶️", style=discord.ButtonStyle.success, row=2)
    async def play_album(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        album = await bot.library.album_by_id(self.album_id)
        if not album:
            await interaction.response.send_message("That album no longer exists.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await queue_pcloud_album(interaction, album)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"Queued the full album **{album['title']}** with {count} tracks."
        )


class FavoriteSelect(discord.ui.Select):
    def __init__(self, rows) -> None:
        options = [
            discord.SelectOption(
                label=row["title"][:100],
                description=row["album_title"][:100],
                value=str(index),
                emoji="▶️" if row["source_type"] == "youtube" else "🎵",
            )
            for index, row in enumerate(rows)
        ]
        super().__init__(placeholder="Choose a favorite to play", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: FavoriteTracksView = self.view  # type: ignore[assignment]
        row = view.rows[int(self.values[0])]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if row["source_type"] == "youtube":
                result = YouTubeResult(
                    row["title"], row["source_url"], row["uploader"],
                    row["duration"], row["thumbnail"],
                )
                message = await queue_youtube_track(interaction, result)
            else:
                track = await bot.library.pcloud_track_by_id(int(row["source_id"]))
                message = await queue_pcloud_track(interaction, track)
        except (ValueError, YouTubeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(message, ephemeral=True)


class FavoriteTracksView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.page = 0
        self.total = 0
        self.rows = []

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
        self.rows = rows
        for child in list(self.children):
            if isinstance(child, FavoriteSelect):
                self.remove_item(child)
        if rows:
            self.add_item(FavoriteSelect(rows))
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
    embed = discord.Embed(
        title=f"{status}: {item.title}", url=item.source_url or None,
        color=discord.Color.purple(),
    )
    embed.add_field(name="Artist", value=item.artist or item.uploader or "Unknown", inline=False)
    embed.add_field(name="Album", value=item.album or "Unknown", inline=False)
    embed.add_field(name="Requested by", value=item.requester, inline=True)
    embed.add_field(name="Up next", value=str(player.queue.qsize()), inline=True)
    embed.add_field(name="Repeat", value=player.repeat_mode.title(), inline=True)
    elapsed = player.elapsed
    if item.duration:
        filled = min(12, int(12 * elapsed / max(1, item.duration)))
        bar = "━" * filled + "●" + "─" * (12 - filled)
        progress = f"{format_duration(elapsed)} {bar} {format_duration(item.duration)}"
    else:
        progress = f"{format_duration(elapsed)} elapsed"
    embed.add_field(name="Progress", value=progress, inline=False)
    if item.source_url:
        source_name = "Watch on YouTube" if item.source_type == "youtube" else "Original Blogspot post"
        embed.add_field(name="Source", value=f"[{source_name}]({item.source_url})", inline=False)
    if item.cover_url:
        embed.set_thumbnail(url=item.cover_url)
    return embed


async def find_track_input(value: str):
    value = value.strip()
    if value.startswith("id:") and value[3:].isdigit():
        return await bot.library.pcloud_track_by_id(int(value[3:]))
    return await bot.library.find_pcloud_track(value)


def format_duration(seconds: int) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


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

    @discord.ui.button(label="Back", emoji="⏮️", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        if not player.previous():
            await interaction.response.send_message("There is no previous track.", ephemeral=True)
            return
        await interaction.response.send_message("Going back to the previous track.", ephemeral=True)

    @discord.ui.button(label="Repeat", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def repeat_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        player.cycle_repeat_mode()
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        player.stop()
        await asyncio.sleep(0.1)
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)

    @discord.ui.button(label="Clear Queue", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def clear_queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        removed = player.clear_queue()
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)
        await interaction.followup.send(
            f"Cleared {removed} queued song{'s' if removed != 1 else ''}. The current song continues.",
            ephemeral=True,
        )

    @discord.ui.button(label="Favorite", emoji="❤️", style=discord.ButtonStyle.secondary)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        item = player.current
        if not item:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if item.source_type == "youtube":
            added = await bot.library.add_youtube_favorite(
                interaction.user.id, item.source_url, item.title, item.uploader,
                item.duration, item.thumbnail,
            )
        elif item.track_id is not None:
            added = await bot.library.add_favorite(interaction.user.id, item.track_id)
        else:
            added = False
        await interaction.response.send_message(
            "Added to your favorites." if added else "That song is already a favorite.",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh progress", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=now_playing_embed(player), view=self)


class QueueSelect(discord.ui.Select):
    def __init__(self, items, offset: int, selected_index: int | None) -> None:
        options = [
            discord.SelectOption(
                label=f"{offset + index + 1}. {item.title}"[:100],
                description=f"Requested by {item.requester}"[:100],
                value=str(offset + index),
                default=(offset + index == selected_index),
            )
            for index, item in enumerate(items)
        ]
        super().__init__(placeholder="Select an upcoming track to reorder", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: QueueReorderView = self.view  # type: ignore[assignment]
        view.selected_index = int(self.values[0])
        await interaction.response.edit_message(embed=view.render(), view=view)


class QueueReorderView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, guild: discord.Guild) -> None:
        super().__init__(timeout=900)
        self.guild = guild
        self.page = 0
        self.selected_index: int | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        player = bot.player(interaction.guild)
        if not interaction.user.voice or not player.voice or interaction.user.voice.channel != player.voice.channel:
            await interaction.response.send_message(
                "Join Daisy's voice channel to reorder the queue.", ephemeral=True
            )
            return False
        return True

    def render(self) -> discord.Embed:
        player = bot.player(self.guild)
        items = player.upcoming(None)
        pages = max(1, math.ceil(len(items) / self.PAGE_SIZE))
        self.page = min(max(self.page, 0), pages - 1)
        start = self.page * self.PAGE_SIZE
        page_items = items[start:start + self.PAGE_SIZE]
        if self.selected_index is not None and self.selected_index >= len(items):
            self.selected_index = None

        for child in list(self.children):
            if isinstance(child, QueueSelect):
                self.remove_item(child)
        if page_items:
            self.add_item(QueueSelect(page_items, start, self.selected_index))

        self.move_up.disabled = self.selected_index is None or self.selected_index == 0
        self.move_down.disabled = (
            self.selected_index is None or self.selected_index >= len(items) - 1
        )
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= pages - 1

        lines = []
        if player.current:
            lines.append(f"**Now:** {player.current.title} — requested by {player.current.requester}")
        lines.extend(
            f"**{start + index}.** {item.title} — {item.requester}"
            for index, item in enumerate(page_items, 1)
        )
        selected = (
            f"Selected: #{self.selected_index + 1}" if self.selected_index is not None
            else "Select an upcoming song to move it."
        )
        embed = discord.Embed(
            title="Music queue", description="\n".join(lines) or "The queue is empty.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{selected} • Page {self.page + 1}/{pages}")
        return embed

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.primary, row=1)
    async def move_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        if self.selected_index is not None:
            self.selected_index = player.move_queue_item(self.selected_index, -1)
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.primary, row=1)
    async def move_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = bot.player(interaction.guild)  # type: ignore[arg-type]
        if self.selected_index is not None:
            self.selected_index = player.move_queue_item(self.selected_index, 1)
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.secondary, row=2)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=self.render(), view=self)


playlist_group = app_commands.Group(name="playlist", description="Manage personal or server playlists")


@playlist_group.command(name="create", description="Create a playlist")
@app_commands.describe(name="Playlist name", scope="personal or server")
@app_commands.choices(scope=[
    app_commands.Choice(name="Personal", value="personal"),
    app_commands.Choice(name="Server", value="server"),
])
async def playlist_create(interaction: discord.Interaction, name: str, scope: str = "personal") -> None:
    if not interaction.guild or scope not in {"personal", "server"}:
        await interaction.response.send_message("Scope must be `personal` or `server`.", ephemeral=True)
        return
    created = await bot.library.create_playlist(
        scope, name, interaction.user.id, interaction.guild.id
    )
    await interaction.response.send_message(
        f"Created {scope} playlist **{name}**." if created
        else "That playlist already exists or its name is invalid.", ephemeral=True,
    )


def queue_item_metadata(item) -> dict:
    return {
        "source_type": item.source_type, "track_id": item.track_id,
        "source_url": item.source_url, "title": item.title,
        "artist": item.artist, "album": item.album, "uploader": item.uploader,
        "duration": item.duration, "thumbnail": item.thumbnail,
    }


@playlist_group.command(name="add", description="Add a song or the current song")
@app_commands.describe(name="Playlist name", query="Catalogue song; omit for current song", scope="personal or server")
@app_commands.choices(scope=[
    app_commands.Choice(name="Personal", value="personal"),
    app_commands.Choice(name="Server", value="server"),
])
async def playlist_add(
    interaction: discord.Interaction, name: str, query: str = "", scope: str = "personal",
) -> None:
    if not interaction.guild or scope not in {"personal", "server"}:
        await interaction.response.send_message("Scope must be `personal` or `server`.", ephemeral=True)
        return
    if query.strip():
        track = await find_track_input(query)
        item = {
            "source_type": "pcloud", "track_id": track["id"] if track else None,
            "source_url": track["post_url"] if track else None,
            "title": track["title"] if track else "", "artist": track["artist"] if track else "",
            "album": track["album_title"] if track else "", "duration": track["duration"] if track else None,
        } if track else None
    else:
        current = bot.player(interaction.guild).current
        item = queue_item_metadata(current) if current else None
    if not item:
        await interaction.response.send_message("No matching or currently playing song.", ephemeral=True)
        return
    added = await bot.library.add_playlist_item(
        scope, name, interaction.user.id, interaction.guild.id, item
    )
    await interaction.response.send_message(
        f"Added **{item['title']}** to **{name}**." if added else "Playlist not found.",
        ephemeral=True,
    )


@playlist_group.command(name="remove", description="Remove a playlist item by position")
@app_commands.choices(scope=[
    app_commands.Choice(name="Personal", value="personal"),
    app_commands.Choice(name="Server", value="server"),
])
async def playlist_remove(
    interaction: discord.Interaction, name: str, position: int, scope: str = "personal",
) -> None:
    if not interaction.guild:
        return
    removed = await bot.library.remove_playlist_item(
        scope, name, position, interaction.user.id, interaction.guild.id
    )
    await interaction.response.send_message(
        "Removed and reordered the playlist." if removed else "Playlist or position not found.",
        ephemeral=True,
    )


@playlist_group.command(name="play", description="Queue every song in a playlist")
@app_commands.choices(scope=[
    app_commands.Choice(name="Personal", value="personal"),
    app_commands.Choice(name="Server", value="server"),
])
async def playlist_play(
    interaction: discord.Interaction, name: str, scope: str = "personal",
) -> None:
    if not interaction.guild:
        return
    rows = await bot.library.playlist_items(
        scope, name, interaction.user.id, interaction.guild.id
    )
    if not rows:
        await interaction.response.send_message("Playlist is empty or not found.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        for row in rows[:100]:
            if row["source_type"] == "youtube":
                result = YouTubeResult(
                    row["resolved_title"], row["source_url"], row["uploader"] or "YouTube",
                    row["resolved_duration"], row["thumbnail"],
                )
                await queue_youtube_track(interaction, result)
            elif row["track_id"]:
                track = await bot.library.pcloud_track_by_id(row["track_id"])
                await queue_pcloud_track(interaction, track)
    except (ValueError, YouTubeError) as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(f"Queued **{name}** with {min(len(rows),100)} songs.")


bot.tree.add_command(playlist_group)


album_group = app_commands.Group(name="album", description="Play an indexed album")


@album_group.command(name="play", description="Queue every track in an indexed album")
@app_commands.describe(name="Album title")
async def album_play(interaction: discord.Interaction, name: str) -> None:
    album = await bot.library.find_pcloud_album(name.strip())
    if not album:
        await interaction.response.send_message("That album is not indexed yet.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        count = await queue_pcloud_album(interaction, album)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(
        f"Queued the full album **{album['title']}** with {count} tracks."
    )


bot.tree.add_command(album_group)


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


@bot.tree.command(description="Search or play a YouTube song")
@app_commands.describe(query="YouTube URL or song name")
async def youtube(interaction: discord.Interaction, query: str) -> None:
    query = query.strip()
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        results = await YouTubeClient.search(query, limit=10)
    except YouTubeError as exc:
        logging.warning("YouTube search failed: %s", exc)
        await interaction.followup.send(
            "YouTube could not return playable results. Try another search or update yt-dlp.",
            ephemeral=True,
        )
        return
    if not results:
        await interaction.followup.send("No YouTube results found.", ephemeral=True)
        return
    view = YouTubeSearchView(results, interaction.user.id)
    await interaction.followup.send(embed=view.embed(query), view=view, ephemeral=True)


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
    track = await find_track_input(query)
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
    if is_youtube_url(query.strip()):
        try:
            result = (await YouTubeClient.search(query.strip(), limit=1))[0]
        except (YouTubeError, IndexError):
            await interaction.response.send_message("Could not read that YouTube video.", ephemeral=True)
            return
        added = await bot.library.add_youtube_favorite(
            interaction.user.id, result.url, result.title, result.uploader,
            result.duration, result.thumbnail,
        )
        await interaction.response.send_message(
            f"Added **{result.title}** to your favorites." if added
            else f"**{result.title}** is already a favorite.", ephemeral=True,
        )
        return
    if not query.strip() and interaction.guild:
        current = bot.player(interaction.guild).current
        if current and current.source_type == "youtube":
            added = await bot.library.add_youtube_favorite(
                interaction.user.id, current.source_url, current.title, current.uploader,
                current.duration, current.thumbnail,
            )
            await interaction.response.send_message(
                f"Added **{current.title}** to your favorites." if added
                else f"**{current.title}** is already a favorite.", ephemeral=True,
            )
            return
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
    if is_youtube_url(query.strip()):
        try:
            canonical_url = (await YouTubeClient.search(query.strip(), limit=1))[0].url
        except (YouTubeError, IndexError):
            canonical_url = query.strip()
        removed = await bot.library.remove_youtube_favorite(interaction.user.id, canonical_url)
        await interaction.response.send_message(
            "Removed the YouTube song from your favorites." if removed
            else "That YouTube song was not in your favorites.", ephemeral=True,
        )
        return
    if not query.strip() and interaction.guild:
        current = bot.player(interaction.guild).current
        if current and current.source_type == "youtube":
            removed = await bot.library.remove_youtube_favorite(
                interaction.user.id, current.source_url
            )
            await interaction.response.send_message(
                f"Removed **{current.title}** from your favorites." if removed
                else "That song was not in your favorites.", ephemeral=True,
            )
            return
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


@bot.tree.command(description="List your personal and this server's playlists")
async def playlists(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    rows = await bot.library.list_playlists(interaction.user.id, interaction.guild.id)
    lines = [
        f"**{row['name']}** — {row['scope']} • {row['item_count']} songs" for row in rows
    ]
    await interaction.response.send_message(
        embed=discord.Embed(
            title="Available playlists", description="\n".join(lines) or "No playlists yet.",
            color=discord.Color.orange(),
        ), ephemeral=True,
    )


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


def history_embed(title: str, rows) -> discord.Embed:
    lines = [
        f"**{index}. {row['title']}** — {row['artist'] or 'Unknown'}\n"
        f"{row['album'] or row['source_type']} • <t:{int(datetime.fromisoformat(row['played_at']).timestamp())}:R>"
        for index, row in enumerate(rows, 1)
    ]
    return discord.Embed(
        title=title, description="\n\n".join(lines) or "Nothing played yet.",
        color=discord.Color.dark_teal(),
    )


@bot.tree.command(description="Show your listening history in this server")
async def history(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    rows = await bot.library.history(interaction.guild.id, interaction.user.id, 15)
    await interaction.response.send_message(embed=history_embed("Your listening history", rows), ephemeral=True)


@bot.tree.command(description="Show recently played songs in this server")
async def recent(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    rows = await bot.library.history(interaction.guild.id, None, 15)
    await interaction.response.send_message(embed=history_embed("Recently played", rows))


top_group = app_commands.Group(name="top", description="Server listening charts")


@top_group.command(name="songs", description="Show the server's most-played songs")
async def top_songs(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    rows = await bot.library.top_history(interaction.guild.id, "song")
    text = "\n".join(f"**{i}. {row['name']}** — {row['plays']} plays" for i,row in enumerate(rows,1))
    await interaction.response.send_message(embed=discord.Embed(title="Top songs",description=text or "No plays yet."))


@top_group.command(name="artists", description="Show the server's most-played artists")
async def top_artists(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    rows = await bot.library.top_history(interaction.guild.id, "artist")
    text = "\n".join(f"**{i}. {row['name']}** — {row['plays']} plays" for i,row in enumerate(rows,1))
    await interaction.response.send_message(embed=discord.Embed(title="Top artists",description=text or "No plays yet."))


my_group = app_commands.Group(name="my", description="Your Daisy profile")


@my_group.command(name="stats", description="Show your listening statistics")
async def my_stats(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    stats = await bot.library.user_stats(interaction.guild.id, interaction.user.id)
    embed = discord.Embed(title=f"{interaction.user.display_name}'s stats",color=discord.Color.magenta())
    embed.add_field(name="Songs started",value=f"{stats['plays']:,}")
    embed.add_field(name="Listening time",value=format_duration(stats['seconds']))
    embed.add_field(name="Top artist",value=stats['top_artist'],inline=False)
    await interaction.response.send_message(embed=embed,ephemeral=True)


bot.tree.add_command(top_group)
bot.tree.add_command(my_group)


@bot.tree.command(name="queue", description="Show the current song and upcoming queue")
async def show_queue(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    view = QueueReorderView(interaction.guild)
    await interaction.response.send_message(embed=view.render(), view=view)


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
    embed.add_field(name="Artists", value=f"{stats['artists']:,}")
    embed.add_field(name="Favorites", value=f"{stats['favorites']:,}")
    embed.add_field(name="Playlists", value=f"{stats['playlists']:,}")
    embed.add_field(name="Recorded plays", value=f"{stats['history']:,}")
    embed.add_field(name="Cached durations", value=f"{stats['durations']:,}")
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


@bot.tree.command(description="Return to the previous completed track")
async def back(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    changed = bot.player(interaction.guild).previous()
    await interaction.response.send_message(
        "Going back to the previous track." if changed else "There is no previous track."
    )


@bot.tree.command(description="Set repeat mode for this server's music queue")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Repeat current track", value="track"),
    app_commands.Choice(name="Repeat queue", value="queue"),
])
async def repeat(interaction: discord.Interaction, mode: str) -> None:
    if not interaction.guild:
        return
    selected = bot.player(interaction.guild).set_repeat_mode(mode)
    await interaction.response.send_message(f"Repeat mode: **{selected.title()}**.")


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


@bot.tree.command(description="Remove upcoming songs but keep the current song playing")
async def clearqueue(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        return
    removed = bot.player(interaction.guild).clear_queue()
    await interaction.response.send_message(
        f"Cleared {removed} queued song{'s' if removed != 1 else ''}. The current song continues."
    )


@bot.tree.command(description="Stop playback and leave voice")
async def leave(interaction: discord.Interaction) -> None:
    if interaction.guild:
        await bot.player(interaction.guild).close()
    await interaction.response.send_message("Disconnected.")


async def track_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    if not current.strip():
        return []
    rows = await bot.library.autocomplete_tracks(current, 25)
    return [
        app_commands.Choice(
            name=f"{row['title']} — {row['album_title']}"[:100], value=f"id:{row['id']}"
        ) for row in rows
    ]


async def artist_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    rows = await bot.library.autocomplete_artists(current, 25)
    return [app_commands.Choice(name=row["artist"][:100],value=row["artist"][:100]) for row in rows]


async def album_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    if not current.strip():
        return []
    rows = await bot.library.autocomplete_albums(current, 25)
    return [
        app_commands.Choice(
            name=f"{row['title']} ({row['track_count']} tracks)"[:100],
            value=row["title"][:100],
        )
        for row in rows
    ]


play.autocomplete("query")(track_autocomplete)
playlist_add.autocomplete("query")(track_autocomplete)
random.autocomplete("artist")(artist_autocomplete)
randomalbum.autocomplete("artist")(artist_autocomplete)
artists.autocomplete("filter_text")(artist_autocomplete)
album_play.autocomplete("name")(album_autocomplete)


@bot.event
async def on_ready() -> None:
    logging.info("Logged in as %s", bot.user)


if __name__ == "__main__":
    if not TOKEN or TOKEN == "paste_your_bot_token_here":
        raise SystemExit("Set DISCORD_TOKEN in .env first.")
    bot.run(TOKEN)
