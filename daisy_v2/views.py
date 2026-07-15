from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from .models import Album, Artist, CatalogTrack, QueueTrack, YouTubeTrack
from .text import display_duration

if TYPE_CHECKING:
    from .app import DaisyV2


def _trim(value: str, length: int = 100) -> str:
    return value if len(value) <= length else value[: length - 1] + "…"


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Run your own command to control this view.", ephemeral=True)
        return False


class CatalogueSelect(discord.ui.Select):
    def __init__(self, tracks: list[CatalogTrack], app: DaisyV2) -> None:
        self.tracks = {str(track.id): track for track in tracks}
        self.app = app
        super().__init__(
            placeholder="Choose a song to queue",
            options=[
                discord.SelectOption(
                    label=_trim(track.title),
                    description=_trim(f"{track.artist} • {track.album}", 100),
                    value=str(track.id),
                )
                for track in tracks
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        track = self.tracks.get(self.values[0])
        if not track:
            await interaction.response.send_message("That search result expired.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.app.queue_catalogue_track(interaction, track)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(message, ephemeral=True)


class TrackSearchView(OwnedView):
    def __init__(self, owner_id: int, tracks: list[CatalogTrack], app: DaisyV2) -> None:
        super().__init__(owner_id)
        if tracks:
            self.add_item(CatalogueSelect(tracks, app))


class YouTubeSelect(discord.ui.Select):
    def __init__(self, tracks: list[YouTubeTrack], app: DaisyV2) -> None:
        self.tracks = {track.url: track for track in tracks}
        self.app = app
        super().__init__(
            placeholder="Choose a YouTube song",
            options=[
                discord.SelectOption(
                    label=_trim(track.title),
                    description=_trim(f"{track.uploader} • {display_duration(track.duration)}"),
                    value=track.url,
                )
                for track in tracks
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        track = self.tracks.get(self.values[0])
        if not track:
            await interaction.response.send_message("That YouTube result expired.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.app.queue_youtube_track(interaction, track)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(message, ephemeral=True)


class YouTubeSearchView(OwnedView):
    def __init__(self, owner_id: int, tracks: list[YouTubeTrack], app: DaisyV2) -> None:
        super().__init__(owner_id)
        self.add_item(YouTubeSelect(tracks, app))


async def now_playing_embed(player) -> discord.Embed:
    current, upcoming, repeat = await player.snapshot()
    if current is None:
        return discord.Embed(title="Nothing is playing", color=discord.Color.dark_grey())
    elapsed = player.elapsed_seconds()
    total = current.duration
    progress = "Buffering…"
    if total:
        width = 16
        done = min(width, int(width * elapsed / max(total, 1)))
        progress = f"{'━' * done}🔘{'─' * (width - done)} {display_duration(elapsed)} / {display_duration(total)}"
    embed = discord.Embed(title="Now Playing", description=f"**{current.title}**", color=discord.Color.teal())
    embed.add_field(name="Artist", value=current.artist or "Unknown")
    embed.add_field(name="Album", value=current.album or "—")
    embed.add_field(name="Requested by", value=current.requester_name)
    embed.add_field(name="Progress", value=progress, inline=False)
    embed.add_field(name="Up next", value=str(len(upcoming)))
    embed.add_field(name="Repeat", value=repeat.value.title())
    if current.source_url:
        embed.add_field(name="Source", value=f"[Open source]({current.source_url})")
    if current.cover_url:
        embed.set_thumbnail(url=current.cover_url)
    embed.set_footer(text="Cache-first playback • temporary files are evicted automatically")
    return embed


class PlayerControls(OwnedView):
    def __init__(self, owner_id: int, app: DaisyV2, guild_id: int) -> None:
        super().__init__(owner_id, timeout=900)
        self.app = app
        self.guild_id = guild_id

    def _player(self):
        return self.app.players.get(self.guild_id)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=await now_playing_embed(self._player()), view=self)

    @discord.ui.button(label="Pause / Resume", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self._player()
        changed = await player.resume() if player.voice and player.voice.is_paused() else await player.pause()
        if not changed:
            await interaction.response.send_message("Nothing is playing or paused.", ephemeral=True)
            return
        await self._refresh(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._player().back():
            await interaction.response.send_message("There is no previous completed track.", ephemeral=True)
            return
        await self._refresh(interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._player().skip():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await self._refresh(interaction)

    @discord.ui.button(label="Repeat", style=discord.ButtonStyle.secondary)
    async def repeat(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        mode = await self._player().cycle_repeat()
        await interaction.response.send_message(f"Repeat: **{mode.value.title()}**", ephemeral=True)

    @discord.ui.button(label="Clear Queue", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        count = await self._player().clear()
        await interaction.response.send_message(f"Removed {count} queued track(s).", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._player().stop()
        await self._refresh(interaction)


def queue_embed(current: QueueTrack | None, upcoming: list[QueueTrack]) -> discord.Embed:
    lines: list[str] = []
    if current:
        lines.append(f"**Now:** {current.title} — {current.artist}")
    for index, track in enumerate(upcoming[:20], start=1):
        lines.append(f"**{index}.** {track.title} — {track.artist}")
    embed = discord.Embed(title="Daisy queue", description="\n".join(lines) or "The queue is empty.", color=discord.Color.blurple())
    if len(upcoming) > 20:
        embed.set_footer(text=f"Showing 20 of {len(upcoming)} upcoming tracks")
    return embed


class QueueSelect(discord.ui.Select):
    def __init__(self, tracks: list[QueueTrack], offset: int, view: QueueReorderView) -> None:
        self.browser = view
        self.offset = offset
        super().__init__(
            placeholder="Select an upcoming track to move",
            options=[
                discord.SelectOption(
                    label=_trim(track.title), description=_trim(f"{track.artist} • queue #{offset + index + 1}"),
                    value=str(offset + index),
                )
                for index, track in enumerate(tracks)
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.browser.selected_index = int(self.values[0])
        await interaction.response.edit_message(embed=await self.browser.render(), view=self.browser)


class QueueReorderView(OwnedView):
    PAGE_SIZE = 20

    def __init__(self, owner_id: int, app: DaisyV2, guild_id: int, page: int = 0) -> None:
        super().__init__(owner_id)
        self.app = app
        self.guild_id = guild_id
        self.page = page
        self.selected_index: int | None = None

    def _player(self):
        return self.app.players.get(self.guild_id)

    async def render(self) -> discord.Embed:
        current, upcoming, _ = await self._player().snapshot()
        pages = max(1, (len(upcoming) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(max(0, self.page), pages - 1)
        start = self.page * self.PAGE_SIZE
        visible = upcoming[start:start + self.PAGE_SIZE]
        if self.selected_index is not None and self.selected_index >= len(upcoming):
            self.selected_index = None
        for child in list(self.children):
            if isinstance(child, QueueSelect):
                self.remove_item(child)
        if visible:
            self.add_item(QueueSelect(visible, start, self))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        self.move_up.disabled = self.selected_index is None or self.selected_index == 0
        self.move_down.disabled = self.selected_index is None or self.selected_index >= len(upcoming) - 1
        lines = []
        if current:
            lines.append(f"**Now:** {current.title} — {current.artist}")
        lines.extend(
            f"**{start + index}.** {track.title} — {track.artist}"
            for index, track in enumerate(visible, 1)
        )
        embed = discord.Embed(title="Daisy queue", description="\n".join(lines) or "The queue is empty.", color=discord.Color.blurple())
        selected = f" • selected #{self.selected_index + 1}" if self.selected_index is not None else ""
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {len(upcoming)} upcoming{selected}")
        return embed

    @discord.ui.button(label="Move Up", style=discord.ButtonStyle.secondary, row=1)
    async def move_up(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.selected_index is not None:
            moved = await self._player().move(self.selected_index, -1)
            if moved is not None:
                self.selected_index = moved
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Move Down", style=discord.ButtonStyle.secondary, row=1)
    async def move_down(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.selected_index is not None:
            moved = await self._player().move(self.selected_index, 1)
            if moved is not None:
                self.selected_index = moved
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)


class ArtistSelect(discord.ui.Select):
    def __init__(self, artists: list[Artist], view: ArtistBrowser) -> None:
        self.artists = {str(artist.id): artist for artist in artists}
        self.browser = view
        super().__init__(
            placeholder="Choose an artist",
            options=[
                discord.SelectOption(
                    label=_trim(artist.name),
                    description=f"{artist.album_count:,} album(s)",
                    value=str(artist.id),
                )
                for artist in artists
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        artist = self.artists.get(self.values[0])
        if not artist:
            await interaction.response.send_message("That artist selection expired.", ephemeral=True)
            return
        view = AlbumBrowser(
            self.browser.owner_id, self.browser.app, artist,
            parent_filter=self.browser.filter_text, parent_page=self.browser.page,
        )
        await interaction.response.edit_message(embed=await view.render(), view=view)


class ArtistBrowser(OwnedView):
    PAGE_SIZE = 20

    def __init__(self, owner_id: int, app: DaisyV2, filter_text: str = "", page: int = 0) -> None:
        super().__init__(owner_id)
        self.app = app
        self.filter_text = filter_text
        self.page = page
        self.total = 0

    async def render(self) -> discord.Embed:
        self.total = await self.app.catalogue.count_artists(self.filter_text)
        pages = max(1, (self.total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(max(0, self.page), pages - 1)
        artists = await self.app.catalogue.artists(
            self.filter_text, limit=self.PAGE_SIZE, offset=self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, ArtistSelect):
                self.remove_item(child)
        if artists:
            self.add_item(ArtistSelect(artists, self))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        embed = discord.Embed(title="Artists", color=discord.Color.teal())
        embed.description = "\n".join(
            f"**{self.page * self.PAGE_SIZE + index}. {artist.name}** — {artist.album_count} album(s)"
            for index, artist in enumerate(artists, 1)
        ) or "No artists matched."
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} artists")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)


class AlbumSelect(discord.ui.Select):
    def __init__(self, albums: list[Album], view: AlbumBrowser) -> None:
        self.albums = {str(album.id): album for album in albums}
        self.browser = view
        super().__init__(
            placeholder="Choose an album",
            options=[
                discord.SelectOption(
                    label=_trim(album.title),
                    description=_trim(f"{album.track_count} track(s)", 100),
                    value=str(album.id),
                )
                for album in albums
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        album = self.albums.get(self.values[0])
        if not album:
            await interaction.response.send_message("That album selection expired.", ephemeral=True)
            return
        view = AlbumTrackBrowser(
            self.browser.owner_id, self.browser.app, album,
            artist=self.browser.artist, parent_page=self.browser.page,
            parent_filter=self.browser.parent_filter, artist_parent_page=self.browser.parent_page,
        )
        await interaction.response.edit_message(embed=await view.render(), view=view)


class AlbumBrowser(OwnedView):
    PAGE_SIZE = 20

    def __init__(
        self, owner_id: int, app: DaisyV2, artist: Artist, *, parent_filter: str, parent_page: int, page: int = 0
    ) -> None:
        super().__init__(owner_id)
        self.app = app
        self.artist = artist
        self.parent_filter = parent_filter
        self.parent_page = parent_page
        self.page = page
        self.total = 0

    async def render(self) -> discord.Embed:
        self.total = await self.app.catalogue.count_albums_for_artist(self.artist.id)
        pages = max(1, (self.total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(max(0, self.page), pages - 1)
        albums = await self.app.catalogue.albums_for_artist(
            self.artist.id, limit=self.PAGE_SIZE, offset=self.page * self.PAGE_SIZE
        )
        for child in list(self.children):
            if isinstance(child, AlbumSelect):
                self.remove_item(child)
        if albums:
            self.add_item(AlbumSelect(albums, self))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        embed = discord.Embed(title=f"Albums: {self.artist.name}"[:256], color=discord.Color.teal())
        embed.description = "\n".join(
            f"**{self.page * self.PAGE_SIZE + index}. {album.title}** — {album.track_count} track(s)"
            for index, album in enumerate(albums, 1)
        ) or "No albums found."
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {self.total:,} albums")
        return embed

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ArtistBrowser(self.owner_id, self.app, self.parent_filter, self.parent_page)
        await interaction.response.edit_message(embed=await view.render(), view=view)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)


class AlbumTrackSelect(discord.ui.Select):
    def __init__(self, tracks: list[CatalogTrack], view: AlbumTrackBrowser) -> None:
        self.tracks = {str(track.id): track for track in tracks}
        self.browser = view
        super().__init__(
            placeholder="Choose a song from this album",
            options=[
                discord.SelectOption(
                    label=_trim(track.title), description=_trim(display_duration(track.duration)), value=str(track.id)
                )
                for track in tracks
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        track = self.tracks.get(self.values[0])
        if not track:
            await interaction.response.send_message("That track selection expired.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.browser.app.queue_catalogue_track(interaction, track)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(message, ephemeral=True)


class AlbumTrackBrowser(OwnedView):
    PAGE_SIZE = 20

    def __init__(
        self, owner_id: int, app: DaisyV2, album: Album, *, artist: Artist,
        parent_page: int, parent_filter: str, artist_parent_page: int, page: int = 0,
    ) -> None:
        super().__init__(owner_id)
        self.app = app
        self.album = album
        self.artist = artist
        self.parent_page = parent_page
        self.parent_filter = parent_filter
        self.artist_parent_page = artist_parent_page
        self.page = page
        self._tracks: list[CatalogTrack] = []

    async def render(self) -> discord.Embed:
        self._tracks = await self.app.catalogue.album_tracks(self.album.id)
        pages = max(1, (len(self._tracks) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(max(0, self.page), pages - 1)
        start = self.page * self.PAGE_SIZE
        visible = self._tracks[start:start + self.PAGE_SIZE]
        for child in list(self.children):
            if isinstance(child, AlbumTrackSelect):
                self.remove_item(child)
        if visible:
            self.add_item(AlbumTrackSelect(visible, self))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1
        embed = discord.Embed(title=self.album.title[:256], color=discord.Color.teal())
        embed.description = "\n".join(
            f"**{start + index}. {track.title}** — {display_duration(track.duration)}"
            for index, track in enumerate(visible, 1)
        ) or "No tracks found."
        if self.album.cover_url:
            embed.set_thumbnail(url=self.album.cover_url)
        embed.set_footer(text=f"Page {self.page + 1}/{pages} • {len(self._tracks)} tracks")
        return embed

    @discord.ui.button(label="Play Album", style=discord.ButtonStyle.success, row=1)
    async def play_album(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            player = await self.app.ensure_player(interaction)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        tracks = await self.app.catalogue.album_tracks(self.album.id)
        await player.enqueue([self.app.queue_from_catalogue(track, interaction) for track in tracks])
        await interaction.followup.send(f"Queued **{self.album.title}** with {len(tracks)} tracks.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = AlbumBrowser(
            self.owner_id, self.app, self.artist, parent_filter=self.parent_filter,
            parent_page=self.artist_parent_page, page=self.parent_page,
        )
        await interaction.response.edit_message(embed=await view.render(), view=view)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=await self.render(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=2)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=await self.render(), view=self)
