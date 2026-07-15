from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shutil
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

import discord

from .cache import DiskCache
from .models import QueueTrack, RepeatMode


LOGGER = logging.getLogger(__name__)
StartCallback = Callable[[QueueTrack], Awaitable[int | None] | int | None]
FinishCallback = Callable[[QueueTrack, int | None, bool], Awaitable[None] | None]


class VoiceConnectionError(ValueError):
    """A user-facing error raised when Discord rejects a voice connection."""


def find_ffmpeg(configured: str | None = None) -> str:
    if configured and Path(configured).is_file():
        return configured
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    local = Path(os.getenv("LOCALAPPDATA", ""))
    matches = sorted((local / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"))
    if matches:
        return str(matches[-1])
    raise FileNotFoundError("FFmpeg was not found. Install it or set FFMPEG_PATH.")


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


class GuildPlayer:
    """Single-guild actor: cache-first playback with explicit queue mutation APIs."""

    def __init__(
        self,
        guild_id: int,
        cache: DiskCache,
        *,
        ffmpeg_path: str | None = None,
        idle_seconds: int = 300,
        prefetch_tracks: int = 1,
        on_start: StartCallback | None = None,
        on_finish: FinishCallback | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.cache = cache
        self.ffmpeg = find_ffmpeg(ffmpeg_path)
        self.idle_seconds = idle_seconds
        self.prefetch_tracks = prefetch_tracks
        self.on_start = on_start
        self.on_finish = on_finish
        self.voice: discord.VoiceClient | None = None
        self.current: QueueTrack | None = None
        self.repeat_mode = RepeatMode.OFF
        self._queue: deque[QueueTrack] = deque()
        self._history: deque[QueueTrack] = deque(maxlen=50)
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._finished: asyncio.Event | None = None
        self._buffer_wait: asyncio.Task[Path] | None = None
        self._generation = 0
        self._skipped = False
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self.started_at: float | None = None

    async def connect(self, channel: discord.VoiceChannel | discord.StageChannel) -> None:
        try:
            async with self._lock:
                voice = self.voice
            if voice and voice.is_connected():
                if voice.channel != channel:
                    await voice.move_to(channel)
            else:
                voice = await channel.connect(reconnect=True, self_deaf=True)
        except (discord.Forbidden, discord.HTTPException, discord.ClientException, asyncio.TimeoutError) as exc:
            raise VoiceConnectionError(
                "I could not join that channel. Allow Daisy View Channel, Connect, and Speak."
            ) from exc
        async with self._lock:
            self.voice = voice
            self._ensure_worker_locked()
            self._wake.set()

    async def enqueue(self, tracks: list[QueueTrack]) -> int:
        if not tracks:
            return 0
        async with self._lock:
            self._queue.extend(tracks)
            position = len(self._queue) + (1 if self.current else 0)
            self._ensure_worker_locked()
            self._wake.set()
            prefetch = list(self._queue)[:self.prefetch_tracks]
        if prefetch:
            await self.cache.prefetch(prefetch)
        return position

    async def enqueue_first(self, track: QueueTrack) -> None:
        async with self._lock:
            self._queue.appendleft(track)
            self._ensure_worker_locked()
            self._wake.set()

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name=f"guild-player:{self.guild_id}")

    async def _next(self) -> QueueTrack | None:
        async with self._lock:
            if self._queue:
                return self._queue.popleft()
            self._wake.clear()
            return None

    async def _wait_for_connection_if_needed(self) -> bool:
        """Park queued playback until a voice connection exists instead of spinning.

        A leave event or dropped voice connection must keep the queue intact, but
        it must not repeatedly pop and re-add the first item while disconnected.
        """
        async with self._lock:
            has_queued_track = bool(self._queue)
            connected = bool(self.voice and self.voice.is_connected())
            if not has_queued_track or connected:
                return True
            self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.idle_seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self) -> None:
        try:
            while True:
                if not await self._wait_for_connection_if_needed():
                    # Keep the queue for a later explicit /join, but stop this
                    # idle worker. connect() will create a new one.
                    await self.disconnect()
                    return
                track = await self._next()
                if track is None:
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=self.idle_seconds)
                        continue
                    except asyncio.TimeoutError:
                        await self.disconnect()
                        return
                await self._play(track)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Guild player %s crashed", self.guild_id)
        finally:
            async with self._lock:
                self._worker = None

    async def _play(self, track: QueueTrack) -> None:
        async with self._lock:
            voice = self.voice
            if not voice or not voice.is_connected():
                self._queue.appendleft(track)
                return
            self.current = track
            self._generation += 1
            generation = self._generation
            self._skipped = False
            self._paused_at = None
            self._paused_total = 0.0
            self.started_at = None
            self.cache.pin(track)

        history_id: int | None = None
        completed = False
        try:
            # V2 deliberately waits for a verified, normalized local file before starting.
            buffer_wait = asyncio.create_task(self.cache.prepare_playback(track), name=f"buffer:{track.source_key}")
            self._buffer_wait = buffer_wait
            try:
                path = await buffer_wait
            except asyncio.CancelledError:
                # A skip/stop cancels only this wait; the shared cache task can
                # finish in the background and remain useful for a later play.
                async with self._lock:
                    if generation != self._generation:
                        return
                raise
            async with self._lock:
                if generation != self._generation or self.current is not track:
                    return
                voice = self.voice
            if not voice or not voice.is_connected():
                return

            finished = asyncio.Event()
            self._finished = finished
            loop = asyncio.get_running_loop()
            source = discord.FFmpegPCMAudio(
                str(path), executable=self.ffmpeg,
                before_options="-nostdin -hide_banner -loglevel error",
                options="-vn",
            )

            def after(error: Exception | None) -> None:
                if error:
                    LOGGER.error("Playback failed for %s: %s", track.title, error)
                loop.call_soon_threadsafe(finished.set)

            voice.play(source, after=after)
            self.started_at = time.monotonic()
            if self.on_start:
                history_id = await _maybe_await(self.on_start(track))
            async with self._lock:
                upcoming = list(self._queue)[:self.prefetch_tracks]
            if upcoming:
                await self.cache.prefetch(upcoming)
            await finished.wait()
            async with self._lock:
                completed = generation == self._generation and not self._skipped
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Could not play %s", track.title)
        finally:
            self.cache.unpin(track)
            if self.on_finish and history_id is not None:
                try:
                    await _maybe_await(self.on_finish(track, history_id, completed))
                except Exception:
                    LOGGER.exception("Could not finalize play history")
            async with self._lock:
                if self.current is track:
                    if completed:
                        self._history.append(replace(track))
                        if self.repeat_mode is RepeatMode.TRACK:
                            self._queue.appendleft(replace(track))
                        elif self.repeat_mode is RepeatMode.QUEUE:
                            self._queue.append(replace(track))
                    self.current = None
                    self.started_at = None
                    self._paused_at = None
                    self._paused_total = 0.0
                    self._finished = None
                    self._buffer_wait = None
                    if self._queue:
                        self._wake.set()

    async def pause(self) -> bool:
        async with self._lock:
            if self.voice and self.voice.is_playing():
                self.voice.pause()
                self._paused_at = time.monotonic()
                return True
        return False

    async def resume(self) -> bool:
        async with self._lock:
            if self.voice and self.voice.is_paused():
                self.voice.resume()
                if self._paused_at is not None:
                    self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = None
                return True
        return False

    async def skip(self) -> bool:
        async with self._lock:
            if not self.current:
                return False
            self._skipped = True
            self._generation += 1
            if self._buffer_wait and not self._buffer_wait.done():
                self._buffer_wait.cancel()
            if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
                self.voice.stop()
            if self._finished:
                self._finished.set()
            return True

    async def back(self) -> bool:
        async with self._lock:
            if not self._history:
                return False
            previous = self._history.pop()
            has_current = self.current is not None
            if has_current:
                self._queue.appendleft(replace(self.current))
            self._queue.appendleft(previous)
            self._ensure_worker_locked()
            self._wake.set()
        if has_current:
            await self.skip()
        return True

    async def clear(self) -> int:
        async with self._lock:
            count = len(self._queue)
            self._queue.clear()
            self.repeat_mode = RepeatMode.OFF
            return count

    async def stop(self) -> None:
        await self.clear()
        await self.skip()

    async def move(self, index: int, delta: int) -> int | None:
        async with self._lock:
            target = index + delta
            if index < 0 or index >= len(self._queue) or target < 0 or target >= len(self._queue):
                return None
            entries = list(self._queue)
            entries[index], entries[target] = entries[target], entries[index]
            self._queue = deque(entries)
            return target

    async def cycle_repeat(self) -> RepeatMode:
        async with self._lock:
            self.repeat_mode = {
                RepeatMode.OFF: RepeatMode.TRACK,
                RepeatMode.TRACK: RepeatMode.QUEUE,
                RepeatMode.QUEUE: RepeatMode.OFF,
            }[self.repeat_mode]
            return self.repeat_mode

    async def snapshot(self) -> tuple[QueueTrack | None, list[QueueTrack], RepeatMode]:
        async with self._lock:
            return self.current, list(self._queue), self.repeat_mode

    def elapsed_seconds(self) -> int:
        if self.started_at is None:
            return 0
        paused = (time.monotonic() - self._paused_at) if self._paused_at else 0
        return max(0, int(time.monotonic() - self.started_at - self._paused_total - paused))

    async def disconnect(self) -> None:
        async with self._lock:
            voice, self.voice = self.voice, None
            self._wake.set()
        if voice and voice.is_connected():
            await voice.disconnect(force=True)

    async def close(self) -> None:
        worker = self._worker
        if worker and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await self.disconnect()


class PlayerManager:
    def __init__(self, factory: Callable[[int], GuildPlayer]) -> None:
        self._factory = factory
        self._players: dict[int, GuildPlayer] = {}

    def get(self, guild_id: int) -> GuildPlayer:
        return self._players.setdefault(guild_id, self._factory(guild_id))

    async def close(self) -> None:
        await asyncio.gather(*(player.close() for player in self._players.values()), return_exceptions=True)
