from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .cache import DiskCache
from .catalogue import Catalogue
from .config import Settings
from .indexer import BlogspotIndexer, SyncResult
from .models import CatalogTrack, QueueTrack, SourceType, YouTubeTrack
from .player import GuildPlayer, PlayerManager, VoiceConnectionError
from .sources import MediaSources, SourceError
from .state import UserState
from .text import display_duration, limited_lines, truncate
from .views import (
    ArtistBrowser,
    PlayerControls,
    QueueReorderView,
    TrackSearchView,
    YouTubeSearchView,
    now_playing_embed,
)


LOGGER = logging.getLogger(__name__)


class DaisyV2(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.catalogue = Catalogue(settings.catalog_db)
        self.indexer = BlogspotIndexer(self.catalogue)
        self.state = UserState(settings.state_db)
        self.sources = MediaSources(settings.youtube_cookies_file)
        self.cache = DiskCache(
            settings.cache_dir, self.sources,
            max_bytes=settings.cache_max_bytes,
            max_track_bytes=settings.cache_max_track_bytes,
            ttl_seconds=settings.cache_ttl_seconds,
            ffmpeg_path=settings.ffmpeg_path,
        )
        self.players = PlayerManager(self._new_player)
        self._sync_lock = asyncio.Lock()
        self._auto_sync_task: asyncio.Task[None] | None = None
        self.last_sync: SyncResult | None = None
        self._register_commands()

    def _new_player(self, guild_id: int) -> GuildPlayer:
        async def on_start(track: QueueTrack) -> int | None:
            return await self.state.record_start(guild_id, track)

        async def on_finish(track: QueueTrack, history_id: int | None, completed: bool) -> None:
            if history_id is not None and completed:
                await self.state.complete(history_id)

        return GuildPlayer(
            guild_id, self.cache, ffmpeg_path=self.settings.ffmpeg_path,
            idle_seconds=self.settings.playback_idle_seconds,
            prefetch_tracks=self.settings.prefetch_tracks,
            on_start=on_start, on_finish=on_finish,
        )

    async def setup_hook(self) -> None:
        await self.catalogue.initialize()
        await self.state.initialize()
        await self.cache.initialize()
        if self.settings.guild_id:
            guild = discord.Object(self.settings.guild_id)
            try:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except discord.Forbidden:
                # A mistyped ID or a bot that has not been invited to the test
                # guild must not prevent the music service from starting.
                LOGGER.warning(
                    "DISCORD_GUILD_ID=%s is not accessible; falling back to global command sync. "
                    "Invite the test bot first or correct the guild ID.",
                    self.settings.guild_id,
                )
                await self.tree.sync()
        else:
            await self.tree.sync()
        self._auto_sync_task = asyncio.create_task(self._auto_sync(), name="daisy-v2-auto-sync")

    async def _auto_sync(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.sync_interval_seconds)
                try:
                    await self.sync_catalogue(max_posts=100)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A brief Blogspot/pCloud outage should not permanently
                    # disable future catalogue checks.
                    LOGGER.exception("Automatic catalogue sync failed; will retry on the next interval")
        except asyncio.CancelledError:
            raise

    async def sync_catalogue(self, *, max_posts: int = 100) -> SyncResult:
        async with self._sync_lock:
            self.last_sync = await self.indexer.sync(max_posts=max_posts, scan_pcloud=True, incremental=True)
            return self.last_sync

    async def close(self) -> None:
        if self._auto_sync_task:
            self._auto_sync_task.cancel()
            await asyncio.gather(self._auto_sync_task, return_exceptions=True)
        await self.players.close()
        await super().close()

    @staticmethod
    def is_admin(interaction: discord.Interaction, role_name: str) -> bool:
        member = interaction.user
        return isinstance(member, discord.Member) and (
            member.guild_permissions.manage_guild or any(role.name == role_name for role in member.roles)
        )

    @staticmethod
    def user_voice_channel(
        interaction: discord.Interaction,
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        member = interaction.user
        if isinstance(member, discord.Member) and member.voice:
            channel = member.voice.channel
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return channel
        return None

    @staticmethod
    async def require_guild(interaction: discord.Interaction) -> bool:
        if interaction.guild:
            return True
        await interaction.response.send_message(
            "Music playback is only available in a Discord server.", ephemeral=True
        )
        return False

    async def ensure_player(self, interaction: discord.Interaction) -> GuildPlayer:
        if not interaction.guild:
            raise ValueError("Music playback is only available in a Discord server.")
        player = self.players.get(interaction.guild.id)
        channel = self.user_voice_channel(interaction)
        if channel:
            await player.connect(channel)
            return player
        if player.voice and player.voice.is_connected():
            return player
        raise ValueError("Join a voice channel first, or use `/join` to choose one manually.")

    @staticmethod
    def queue_from_catalogue(track: CatalogTrack, interaction: discord.Interaction) -> QueueTrack:
        return QueueTrack(
            source_type=SourceType.PCLOUD, source_key=track.cache_key,
            title=track.title, artist=track.artist, album=track.album,
            requester_id=interaction.user.id, requester_name=interaction.user.display_name,
            duration=track.duration, source_url=track.post_url, cover_url=track.cover_url,
            catalog_track_id=track.id, pcloud_code=track.pcloud_code, pcloud_file_id=track.pcloud_file_id,
            mediafire_quick_key=track.mediafire_quick_key, mediafire_url=track.mediafire_url,
        )

    @staticmethod
    def queue_from_youtube(track: YouTubeTrack, interaction: discord.Interaction) -> QueueTrack:
        return QueueTrack(
            source_type=SourceType.YOUTUBE, source_key=track.cache_key,
            title=track.title, artist=track.uploader, album="YouTube",
            requester_id=interaction.user.id, requester_name=interaction.user.display_name,
            duration=track.duration, source_url=track.url, cover_url=track.thumbnail,
            youtube_url=track.url, uploader=track.uploader,
        )

    @staticmethod
    def queue_from_state_row(row, interaction: discord.Interaction) -> QueueTrack:
        source_type = SourceType(row["source_type"])
        source_key = str(row["source_key"])
        pcloud_code: str | None = None
        pcloud_file_id: int | None = None
        youtube_url: str | None = None
        mediafire_quick_key: str | None = None
        if source_type is SourceType.PCLOUD:
            if source_key.startswith("mediafire:"):
                mediafire_quick_key = source_key.split(":", 1)[1]
                pcloud_code = "mediafire-only"
                pcloud_file_id = 0
            else:
                _, pcloud_code, file_id = source_key.split(":", 2)
                pcloud_file_id = int(file_id)
        else:
            youtube_url = str(row["source_url"])
        return QueueTrack(
            source_type=source_type, source_key=source_key, title=row["title"], artist=row["artist"],
            album=row["album"], requester_id=interaction.user.id, requester_name=interaction.user.display_name,
            duration=row["duration"], source_url=row["source_url"], cover_url=row["thumbnail"],
            pcloud_code=pcloud_code, pcloud_file_id=pcloud_file_id,
            mediafire_quick_key=mediafire_quick_key,
            youtube_url=youtube_url, uploader=row["artist"] if source_type is SourceType.YOUTUBE else "",
        )

    async def queue_catalogue_track(self, interaction: discord.Interaction, track: CatalogTrack) -> str:
        player = await self.ensure_player(interaction)
        item = self.queue_from_catalogue(track, interaction)
        position = await player.enqueue([item])
        return f"Queued **{track.title}**\nAlbum: {track.album}\nQueue position: {position}"

    async def queue_youtube_track(self, interaction: discord.Interaction, track: YouTubeTrack) -> str:
        player = await self.ensure_player(interaction)
        item = self.queue_from_youtube(track, interaction)
        position = await player.enqueue([item])
        return f"Queued **{track.title}**\nChannel: {track.uploader}\nQueue position: {position}"

    def _register_commands(self) -> None:
        @self.tree.command(name="join", description="Connect Daisy to your current voice or Stage channel")
        async def join(interaction: discord.Interaction) -> None:
            if not interaction.guild:
                await interaction.response.send_message("Music playback is only available in a server.", ephemeral=True)
                return
            channel = self.user_voice_channel(interaction)
            if channel is None:
                await interaction.response.send_message(
                    "Join a voice or Stage channel first, then run `/join` again.", ephemeral=True
                )
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                await self.players.get(interaction.guild.id).connect(channel)
            except VoiceConnectionError as exc:
                LOGGER.warning("Could not join channel %s: %s", channel.id, exc)
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(f"Joined **{channel.name}**. You can now queue music.", ephemeral=True)

        @self.tree.command(name="search", description="Search the indexed Burmese music catalogue")
        @app_commands.describe(query="Song, artist, or album name")
        async def search(interaction: discord.Interaction, query: str) -> None:
            tracks = await self.catalogue.search_tracks(query, limit=10)
            if not tracks:
                await interaction.response.send_message(
                    "No matching tracks yet. Run `/sync` or `python -m daisy_v2.indexer --all` first.",
                    ephemeral=True,
                )
                return
            embed = discord.Embed(title=truncate(f"Search: {query}", 256), color=discord.Color.teal())
            embed.description = limited_lines(
                (
                    f"**{index}. {truncate(track.title, 220)}**\n"
                    f"{truncate(track.artist, 120)} — {truncate(track.album, 180)} "
                    f"({display_duration(track.duration)})"
                    for index, track in enumerate(tracks, 1)
                ),
                limit=3_900,
            )
            embed.set_footer(text="Choose a matching song below")
            await interaction.response.send_message(
                embed=embed, view=TrackSearchView(interaction.user.id, tracks, self), ephemeral=True
            )

        @self.tree.command(name="play", description="Queue the first matching indexed song")
        @app_commands.describe(query="Song name, artist, or id:123")
        async def play(interaction: discord.Interaction, query: str) -> None:
            if query.casefold().startswith("id:"):
                try:
                    track = await self.catalogue.track(int(query.split(":", 1)[1].strip()))
                except ValueError:
                    track = None
            else:
                found = await self.catalogue.search_tracks(query, limit=1)
                track = found[0] if found else None
            if not track:
                await interaction.response.send_message("No matching indexed track. Try `/search`.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                message = await self.queue_catalogue_track(interaction, track)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(message + "\nBuffering safely before playback…", ephemeral=True)

        @play.autocomplete("query")
        async def play_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            rows = await self.catalogue.autocomplete_tracks(current, limit=25)
            return [
                app_commands.Choice(name=f"{row.title} — {row.artist}"[:100], value=f"id:{row.id}")
                for row in rows
            ]

        @self.tree.command(name="album", description="Queue every indexed track in an album")
        @app_commands.describe(name="Album title")
        async def album(interaction: discord.Interaction, name: str) -> None:
            matches = await self.catalogue.search_albums(name, limit=1)
            record = matches[0] if matches else None
            if not record:
                await interaction.response.send_message("That album is not in the V2 catalogue.", ephemeral=True)
                return
            tracks = await self.catalogue.album_tracks(record.id)
            if not tracks:
                await interaction.response.send_message("That album has no playable tracks.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            try:
                player = await self.ensure_player(interaction)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            queue = [self.queue_from_catalogue(track, interaction) for track in tracks]
            await player.enqueue(queue)
            await interaction.followup.send(
                f"Queued **{record.title}** with {len(queue)} tracks. The first track is buffering safely."
            )

        @album.autocomplete("name")
        async def album_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            rows = await self.catalogue.search_albums(current, limit=25)
            return [
                app_commands.Choice(name=f"{row.title} — {row.artist}"[:100], value=row.title[:100])
                for row in rows
            ]

        @self.tree.command(name="artists", description="Browse Artist → Album → Track")
        @app_commands.describe(filter_text="Optional part of an artist name")
        async def artists(interaction: discord.Interaction, filter_text: str = "") -> None:
            view = ArtistBrowser(interaction.user.id, self, filter_text)
            await interaction.response.send_message(embed=await view.render(), view=view, ephemeral=True)

        @artists.autocomplete("filter_text")
        async def artists_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            rows = await self.catalogue.autocomplete_artists(current, limit=25)
            return [app_commands.Choice(name=row.name[:100], value=row.name[:100]) for row in rows]

        @self.tree.command(name="youtube", description="Search YouTube and select audio to queue")
        @app_commands.describe(query="YouTube URL or song name")
        async def youtube(interaction: discord.Interaction, query: str) -> None:
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                tracks = await self.sources.search_youtube(query, limit=10)
            except SourceError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            if not tracks:
                await interaction.followup.send("No YouTube results found.", ephemeral=True)
                return
            embed = discord.Embed(title=f"YouTube: {query}"[:256], color=discord.Color.red())
            embed.description = "\n\n".join(
                f"**{index}. {track.title}**\n{track.uploader} — {display_duration(track.duration)}"
                for index, track in enumerate(tracks, 1)
            )
            if tracks[0].thumbnail:
                embed.set_thumbnail(url=tracks[0].thumbnail)
            await interaction.followup.send(embed=embed, view=YouTubeSearchView(interaction.user.id, tracks, self), ephemeral=True)

        @self.tree.command(name="queue", description="Show the current queue")
        async def queue(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            view = QueueReorderView(interaction.user.id, self, interaction.guild.id)
            await interaction.response.send_message(embed=await view.render(), view=view)

        @self.tree.command(name="nowplaying", description="Show current playback and controls")
        async def nowplaying(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            player = self.players.get(interaction.guild.id)
            await interaction.response.send_message(
                embed=await now_playing_embed(player), view=PlayerControls(interaction.user.id, self, interaction.guild.id)
            )

        @self.tree.command(name="pause", description="Pause the current track")
        async def pause(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            changed = await self.players.get(interaction.guild.id).pause()
            await interaction.response.send_message("Paused." if changed else "Nothing is playing.")

        @self.tree.command(name="resume", description="Resume the current track")
        async def resume(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            changed = await self.players.get(interaction.guild.id).resume()
            await interaction.response.send_message("Resumed." if changed else "Nothing is paused.")

        @self.tree.command(name="skip", description="Skip the current track")
        async def skip(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            changed = await self.players.get(interaction.guild.id).skip()
            await interaction.response.send_message("Skipped." if changed else "Nothing is playing.")

        @self.tree.command(name="back", description="Return to the previous completed track")
        async def back(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            changed = await self.players.get(interaction.guild.id).back()
            await interaction.response.send_message("Going back." if changed else "There is no previous completed track.")

        @self.tree.command(name="repeat", description="Cycle repeat: off, track, queue")
        async def repeat(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            mode = await self.players.get(interaction.guild.id).cycle_repeat()
            await interaction.response.send_message(f"Repeat mode: **{mode.value.title()}**")

        @self.tree.command(name="clearqueue", description="Remove all upcoming tracks")
        async def clearqueue(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            count = await self.players.get(interaction.guild.id).clear()
            await interaction.response.send_message(f"Removed {count} upcoming track(s).")

        @self.tree.command(name="stop", description="Stop playback and clear the queue")
        async def stop(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            await self.players.get(interaction.guild.id).stop()
            await interaction.response.send_message("Stopped playback and cleared the queue.")

        @self.tree.command(name="leave", description="Disconnect Daisy from voice")
        async def leave(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            await self.players.get(interaction.guild.id).disconnect()
            await interaction.response.send_message("Disconnected.")

        @self.tree.command(name="favorite", description="Save the currently playing song")
        async def favorite(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            current, _, _ = await self.players.get(interaction.guild.id).snapshot()
            if not current:
                await interaction.response.send_message("Nothing is playing.", ephemeral=True)
                return
            added = await self.state.add_favorite(interaction.user.id, current)
            await interaction.response.send_message("Saved to favorites." if added else "It is already in your favorites.", ephemeral=True)

        @self.tree.command(name="favorites", description="List your V2 favorites")
        async def favorites(interaction: discord.Interaction) -> None:
            rows = await self.state.list_favorites(interaction.user.id)
            if not rows:
                await interaction.response.send_message("You have no V2 favorites yet.", ephemeral=True)
                return
            text = "\n".join(f"**{index}.** {row['title']} — {row['artist']}" for index, row in enumerate(rows, 1))
            await interaction.response.send_message(embed=discord.Embed(title="Your favorites", description=text, color=discord.Color.gold()), ephemeral=True)

        @self.tree.command(name="playfavorite", description="Queue one of your favorites by its /favorites number")
        @app_commands.describe(position="Number shown by /favorites")
        async def playfavorite(interaction: discord.Interaction, position: int) -> None:
            rows = await self.state.list_favorites(interaction.user.id)
            if position < 1 or position > len(rows):
                await interaction.response.send_message("That favorite number does not exist.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                item = self.queue_from_state_row(rows[position - 1], interaction)
                player = await self.ensure_player(interaction)
                queue_position = await player.enqueue([item])
            except (ValueError, SourceError) as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(f"Queued **{item.title}** at position {queue_position}.", ephemeral=True)

        @self.tree.command(name="unfavorite", description="Remove the currently playing track from your favorites")
        async def unfavorite(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            current, _, _ = await self.players.get(interaction.guild.id).snapshot()
            if not current:
                await interaction.response.send_message("Nothing is playing.", ephemeral=True)
                return
            removed = await self.state.remove_favorite(interaction.user.id, current.source_type, current.source_key)
            await interaction.response.send_message("Removed from favorites." if removed else "It was not in your favorites.", ephemeral=True)

        @self.tree.command(name="random", description="Queue a random indexed song")
        @app_commands.describe(artist="Optional artist filter")
        async def random_track(interaction: discord.Interaction, artist: str = "") -> None:
            track = await self.catalogue.random_track(artist)
            if not track:
                await interaction.response.send_message("No matching indexed track.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                message = await self.queue_catalogue_track(interaction, track)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send("🎲 " + message, ephemeral=True)

        @self.tree.command(name="randomalbum", description="Queue a random indexed album")
        @app_commands.describe(artist="Optional artist filter")
        async def random_album(interaction: discord.Interaction, artist: str = "") -> None:
            album_record = await self.catalogue.random_album(artist)
            if not album_record:
                await interaction.response.send_message("No matching indexed album.", ephemeral=True)
                return
            tracks = await self.catalogue.album_tracks(album_record.id)
            if not tracks:
                await interaction.response.send_message("That album has no playable tracks.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            try:
                player = await self.ensure_player(interaction)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await player.enqueue([self.queue_from_catalogue(track, interaction) for track in tracks])
            await interaction.followup.send(f"🎲 Queued **{album_record.title}** with {len(tracks)} tracks.")

        @self.tree.command(name="move", description="Move a queued song up or down")
        @app_commands.describe(position="Upcoming queue position (1 is next)", direction="up or down")
        @app_commands.choices(direction=[
            app_commands.Choice(name="Up", value="up"),
            app_commands.Choice(name="Down", value="down"),
        ])
        async def move(interaction: discord.Interaction, position: int, direction: str) -> None:
            if not await self.require_guild(interaction):
                return
            result = await self.players.get(interaction.guild.id).move(position - 1, -1 if direction == "up" else 1)
            await interaction.response.send_message(
                f"Moved to queue position {result + 1}." if result is not None else "That move is not possible.",
                ephemeral=True,
            )

        playlist_group = app_commands.Group(name="playlist", description="Manage personal and server playlists")
        playlist_scopes = [
            app_commands.Choice(name="Personal (only you)", value="personal"),
            app_commands.Choice(name="Server (shared)", value="server"),
        ]

        @playlist_group.command(name="create", description="Create a playlist")
        @app_commands.describe(scope="personal or server", name="Playlist name")
        @app_commands.choices(scope=playlist_scopes)
        async def playlist_create(interaction: discord.Interaction, scope: str, name: str) -> None:
            if not interaction.guild:
                await interaction.response.send_message("Playlists are available in a server.", ephemeral=True)
                return
            try:
                created = await self.state.create_playlist(scope.casefold(), name, interaction.user.id, interaction.guild.id)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            await interaction.response.send_message("Playlist created." if created else "That playlist already exists.", ephemeral=True)

        @playlist_group.command(name="add", description="Add the currently playing track to a playlist")
        @app_commands.describe(scope="personal or server", name="Playlist name")
        @app_commands.choices(scope=playlist_scopes)
        async def playlist_add(interaction: discord.Interaction, scope: str, name: str) -> None:
            if not await self.require_guild(interaction):
                return
            current, _, _ = await self.players.get(interaction.guild.id).snapshot()
            if not current:
                await interaction.response.send_message("Nothing is playing.", ephemeral=True)
                return
            try:
                added = await self.state.add_playlist_item(
                    scope.casefold(), name, interaction.user.id, interaction.guild.id, current
                )
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            await interaction.response.send_message("Added to playlist." if added else "Playlist not found.", ephemeral=True)

        @playlist_group.command(name="remove", description="Remove an item from a playlist")
        @app_commands.describe(scope="personal or server", name="Playlist name", position="Item position")
        @app_commands.choices(scope=playlist_scopes)
        async def playlist_remove(interaction: discord.Interaction, scope: str, name: str, position: int) -> None:
            if not await self.require_guild(interaction):
                return
            try:
                removed = await self.state.remove_playlist_item(
                    scope.casefold(), name, position, interaction.user.id, interaction.guild.id
                )
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            await interaction.response.send_message("Removed." if removed else "Playlist item not found.", ephemeral=True)

        @playlist_group.command(name="play", description="Queue every track in a playlist")
        @app_commands.describe(scope="personal or server", name="Playlist name")
        @app_commands.choices(scope=playlist_scopes)
        async def playlist_play(interaction: discord.Interaction, scope: str, name: str) -> None:
            if not await self.require_guild(interaction):
                return
            try:
                rows = await self.state.playlist_items(
                    scope.casefold(), name, interaction.user.id, interaction.guild.id
                )
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
            if not rows:
                await interaction.response.send_message("Playlist is empty or not found.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            try:
                player = await self.ensure_player(interaction)
                tracks = [self.queue_from_state_row(row, interaction) for row in rows]
                await player.enqueue(tracks)
            except (ValueError, SourceError) as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(f"Queued **{name}** with {len(tracks)} track(s).")

        self.tree.add_command(playlist_group)

        @self.tree.command(name="playlists", description="List your personal and this server's playlists")
        async def playlists(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            rows = await self.state.list_playlists(interaction.user.id, interaction.guild.id)
            text = "\n".join(
                f"**{row['scope'].title()}: {row['name']}** — {row['track_count']} track(s)" for row in rows
            ) or "No playlists yet."
            await interaction.response.send_message(embed=discord.Embed(title="Playlists", description=text, color=discord.Color.purple()), ephemeral=True)

        @self.tree.command(name="history", description="Show your completed listening history")
        async def history(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            rows = await self.state.history(interaction.guild.id, interaction.user.id)
            text = "\n".join(f"**{index}.** {row['title']} — {row['artist']}" for index, row in enumerate(rows, 1)) or "No completed plays yet."
            await interaction.response.send_message(embed=discord.Embed(title="Your listening history", description=text, color=discord.Color.blurple()), ephemeral=True)

        @self.tree.command(name="recent", description="Show this server's recent completed plays")
        async def recent(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            rows = await self.state.history(interaction.guild.id)
            text = "\n".join(f"**{index}.** {row['title']} — {row['artist']}" for index, row in enumerate(rows, 1)) or "No completed plays yet."
            await interaction.response.send_message(embed=discord.Embed(title="Recent plays", description=text, color=discord.Color.blurple()))

        @self.tree.command(name="topsongs", description="Show this server's most played songs")
        async def topsongs(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            rows = await self.state.top(interaction.guild.id, "title")
            text = "\n".join(f"**{index}.** {row['title']} — {row['plays']} plays" for index, row in enumerate(rows, 1)) or "No completed plays yet."
            await interaction.response.send_message(embed=discord.Embed(title="Top songs", description=text, color=discord.Color.gold()))

        @self.tree.command(name="topartists", description="Show this server's most played artists")
        async def topartists(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            rows = await self.state.top(interaction.guild.id, "artist")
            text = "\n".join(f"**{index}.** {row['artist'] or 'Unknown'} — {row['plays']} plays" for index, row in enumerate(rows, 1)) or "No completed plays yet."
            await interaction.response.send_message(embed=discord.Embed(title="Top artists", description=text, color=discord.Color.gold()))

        @self.tree.command(name="mystats", description="Show your listening statistics")
        async def mystats(interaction: discord.Interaction) -> None:
            if not await self.require_guild(interaction):
                return
            stats = await self.state.stats(interaction.guild.id, interaction.user.id)
            embed = discord.Embed(title="Your listening statistics", color=discord.Color.gold())
            embed.add_field(name="Completed plays", value=str(stats["plays"]))
            embed.add_field(name="Listening time", value=display_duration(stats["seconds"]))
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(name="sync", description="Index recent authorized Blogspot posts and pCloud folders")
        async def sync(interaction: discord.Interaction) -> None:
            if not self.is_admin(interaction, self.settings.admin_role):
                await interaction.response.send_message(
                    f"The `{self.settings.admin_role}` role or Manage Server is required.", ephemeral=True
                )
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                result = await self.sync_catalogue(max_posts=100)
            except Exception as exc:
                LOGGER.exception("Manual catalogue sync failed")
                await interaction.followup.send(f"Catalogue sync failed: {type(exc).__name__}", ephemeral=True)
                return
            await interaction.followup.send(
                f"Indexed {result.posts} posts, {result.albums} albums, and {result.tracks} tracks; "
                f"{result.failed} pCloud links failed.", ephemeral=True
            )

        @self.tree.command(name="status", description="Show Daisy V2 health and catalogue state")
        async def status(interaction: discord.Interaction) -> None:
            stats = await self.catalogue.stats()
            embed = discord.Embed(title="Daisy V2 status", color=discord.Color.green())
            for label, value in stats.items():
                embed.add_field(name=label.title(), value=f"{value:,}")
            embed.add_field(name="Cache limit", value=f"{self.settings.cache_max_bytes // (1024 * 1024)} MB")
            embed.add_field(name="Discord latency", value=f"{self.latency * 1000:.0f} ms")
            await interaction.response.send_message(embed=embed, ephemeral=True)
