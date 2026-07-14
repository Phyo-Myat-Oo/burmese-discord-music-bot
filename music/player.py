from __future__ import annotations

import asyncio
import json
import logging
import os
import queue as thread_queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable

import discord

from music.cache import StreamCache

LOGGER = logging.getLogger(__name__)


@dataclass
class QueueItem:
    source: Path | Callable[[], Awaitable[str | dict]]
    title: str
    album: str = ""
    requester: str = "Unknown"
    track_id: int | None = None
    source_type: str = "pcloud"
    source_url: str = ""
    uploader: str = ""
    duration: int | None = None
    thumbnail: str | None = None
    artist: str = ""
    cover_url: str | None = None
    history_id: int | None = None
    requester_id: int | None = None


def find_ffmpeg() -> str:
    configured = os.getenv("FFMPEG_PATH")
    if configured and Path(configured).is_file():
        return configured
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    local = Path(os.getenv("LOCALAPPDATA", ""))
    winget = local / "Microsoft" / "WinGet" / "Packages"
    matches = sorted(winget.glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"), reverse=True)
    if matches:
        return str(matches[0])
    raise FileNotFoundError("FFmpeg was not found. Install Gyan.FFmpeg or set FFMPEG_PATH in .env.")


def probe_duration(source: str, ffmpeg: str) -> int | None:
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"))
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", source],
            capture_output=True, text=True, timeout=20, check=True,
        )
        value = json.loads(result.stdout).get("format", {}).get("duration")
        return int(float(value)) if value else None
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def prebuffer_frame_count() -> int:
    """Return a bounded number of 20 ms Opus frames to hold before playback."""
    try:
        seconds = float(os.getenv("AUDIO_PREBUFFER_SECONDS", "2"))
    except ValueError:
        seconds = 2.0
    return max(0, min(250, round(seconds * 50)))


def audio_output_mode() -> str:
    """Return the Discord audio input format selected by configuration.

    PCM lets discord.py encode consistently sized 20 ms frames itself.  It uses
    a little more CPU than passing FFmpeg's Opus packets through, but is often
    more tolerant of irregular packet boundaries from remote HTTP streams.
    """
    mode = os.getenv("AUDIO_OUTPUT_MODE", "pcm").strip().casefold()
    if mode not in {"pcm", "opus"}:
        LOGGER.warning("Unknown AUDIO_OUTPUT_MODE=%r; using pcm", mode)
        return "pcm"
    return mode


class BufferedOpusAudio(discord.AudioSource):
    """Keep a short in-memory buffer between FFmpeg and Discord's voice thread.

    Despite the historical name, the wrapper works for both Opus and raw PCM
    sources; ``is_opus`` is delegated to the wrapped FFmpeg source.
    """

    def __init__(self, source: discord.AudioSource, frame_count: int) -> None:
        self.source = source
        self.frame_count = frame_count
        self.frames: thread_queue.Queue[bytes | None] = thread_queue.Queue(
            maxsize=max(frame_count * 2, frame_count + 1)
        )
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._pump, name="daisy-audio-buffer", daemon=True)
        self.worker.start()

    def _put(self, frame: bytes | None) -> bool:
        while not self.stopped.is_set():
            try:
                self.frames.put(frame, timeout=0.1)
                return True
            except thread_queue.Full:
                continue
        return False

    def _pump(self) -> None:
        try:
            while not self.stopped.is_set():
                frame = self.source.read()
                if not frame:
                    break
                if not self._put(frame):
                    return
                if self.frames.qsize() >= self.frame_count:
                    self.ready.set()
        except Exception:
            LOGGER.exception("Audio pre-buffer stopped unexpectedly")
        finally:
            self.ready.set()
            self._put(None)

    def read(self) -> bytes:
        self.ready.wait(timeout=10)
        if self.stopped.is_set():
            return b""
        try:
            frame = self.frames.get(timeout=15)
        except thread_queue.Empty:
            LOGGER.warning("Audio pre-buffer ran dry")
            return b""
        return frame or b""

    def is_opus(self) -> bool:
        return self.source.is_opus()

    def cleanup(self) -> None:
        self.stopped.set()
        self.ready.set()
        self.source.cleanup()


class GuildPlayer:
    def __init__(self, guild: discord.Guild, on_start=None, on_finish=None, on_duration=None,
                 cache_dir: Path | None = None) -> None:
        self.guild = guild
        self.voice: discord.VoiceClient | None = None
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.ffmpeg = find_ffmpeg()
        self.current: QueueItem | None = None
        self.on_start = on_start
        self.on_finish = on_finish
        self.on_duration = on_duration
        self.started_at: float | None = None
        self.paused_at: float | None = None
        self.paused_total = 0.0
        self._was_skipped = False
        self.repeat_mode = "off"
        self.history: list[QueueItem] = []
        _cache_dir = cache_dir or Path(os.getenv("AUDIO_CACHE_DIR", "data/audio_cache"))
        self.cache = StreamCache(_cache_dir)

    @staticmethod
    def _replay_item(item: QueueItem) -> QueueItem:
        """Copy a queue item without reusing its playback-history record."""
        return replace(item, history_id=None)

    def _put_first(self, item: QueueItem) -> None:
        self.queue._queue.appendleft(item)

    def cycle_repeat_mode(self) -> str:
        self.repeat_mode = {"off": "track", "track": "queue", "queue": "off"}[self.repeat_mode]
        return self.repeat_mode

    def set_repeat_mode(self, mode: str) -> str:
        if mode not in {"off", "track", "queue"}:
            raise ValueError("Repeat mode must be off, track, or queue.")
        self.repeat_mode = mode
        return self.repeat_mode

    def previous(self) -> bool:
        """Return to the most recently completed track, keeping this one queued."""
        if not self.history:
            return False
        self.cache.cancel()
        previous_item = self._replay_item(self.history.pop())
        if self.current:
            self._put_first(self._replay_item(self.current))
        self._put_first(previous_item)
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self._was_skipped = True
            self.voice.stop()
        return True

    async def connect(self, channel: discord.VoiceChannel | discord.StageChannel) -> None:
        if self.voice and self.voice.is_connected():
            if self.voice.channel != channel:
                await self.voice.move_to(channel)
        else:
            self.voice = await channel.connect()

    async def enqueue(self, path: Path, title: str) -> None:
        await self.queue.put(QueueItem(path, title))
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self._run())

    async def enqueue_stream(
        self, resolver: Callable[[], Awaitable[str | dict]], title: str,
        album: str = "", requester: str = "Unknown", track_id: int | None = None,
        source_type: str = "pcloud", source_url: str = "", uploader: str = "",
        duration: int | None = None, thumbnail: str | None = None,
        artist: str = "", cover_url: str | None = None,
        requester_id: int | None = None,
    ) -> int:
        await self.queue.put(QueueItem(
            source=resolver, title=title, album=album, requester=requester,
            track_id=track_id, source_type=source_type, source_url=source_url,
            uploader=uploader, duration=duration, thumbnail=thumbnail,
            artist=artist, cover_url=cover_url, requester_id=requester_id,
        ))
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self._run())
        return self.queue.qsize() + (1 if self.current else 0)

    async def _run(self) -> None:
        while self.voice and self.voice.is_connected():
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=300)
            except asyncio.TimeoutError:
                await self.close()
                return
            self.current = item
            self._was_skipped = False
            played = False
            prefetched_file: Path | None = None
            try:
                finished = asyncio.Event()
                loop = asyncio.get_running_loop()

                # Attempt to use a pre-downloaded cached file; fall back to live resolution.
                prefetched_file = await self.cache.consume(item)

                if prefetched_file is not None:
                    source_value = str(prefetched_file)
                    headers: dict = {}
                    before_opts = "-nostdin -hide_banner -loglevel error"
                else:
                    resolved = await item.source() if callable(item.source) else item.source
                    if isinstance(resolved, dict):
                        source_value = resolved["url"]
                        headers = resolved.get("headers", {})
                    else:
                        source_value = str(resolved)
                        headers = {}
                    before_opts = (
                        "-nostdin -hide_banner -loglevel error -reconnect 1 "
                        "-reconnect_streamed 1 -reconnect_delay_max 5"
                    )
                    if headers:
                        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
                        before_opts = f'-headers {shlex.quote(header_str)} ' + before_opts

                if item.duration is None:
                    item.duration = await asyncio.to_thread(probe_duration, source_value, self.ffmpeg)
                    if item.duration and self.on_duration:
                        try:
                            await self.on_duration(item)
                        except Exception:
                            LOGGER.exception("Could not cache duration for %s", item.title)

                output_mode = audio_output_mode()
                audio_source_class = (
                    discord.FFmpegPCMAudio if output_mode == "pcm"
                    else discord.FFmpegOpusAudio
                )
                source = audio_source_class(
                    source_value,
                    executable=self.ffmpeg,
                    before_options=before_opts,
                    options="-vn -af aresample=async=1:first_pts=0",
                )
                buffer_frames = prebuffer_frame_count()
                if buffer_frames:
                    source = BufferedOpusAudio(source, buffer_frames)

                def after(error: Exception | None) -> None:
                    if error:
                        LOGGER.error("Voice playback failed for %s: %s", item.title, error)
                    loop.call_soon_threadsafe(finished.set)

                self.voice.play(source, after=after)
                played = True
                self.started_at = time.monotonic()
                self.paused_at = None
                self.paused_total = 0.0

                # Pre-download the next track while this one plays.
                upcoming = list(self.queue._queue)
                if upcoming:
                    await self.cache.prefetch(upcoming[0])

                if self.on_start:
                    try:
                        item.history_id = await self.on_start(item)
                    except Exception:
                        LOGGER.exception("Could not record history for %s", item.title)
                await finished.wait()
            except Exception:
                LOGGER.exception("Could not start playback for %s", item.title)
            finally:
                # Delete the temporary pre-downloaded file now that playback has ended.
                if prefetched_file is not None:
                    StreamCache.delete_file(prefetched_file)
                completed = played and not self._was_skipped
                if self.on_finish and item.history_id:
                    try:
                        await self.on_finish(item, completed)
                    except Exception:
                        LOGGER.exception("Could not complete history for %s", item.title)
                if completed:
                    self.history.append(self._replay_item(item))
                    del self.history[:-50]
                    if self.repeat_mode == "track":
                        self._put_first(self._replay_item(item))
                    elif self.repeat_mode == "queue":
                        await self.queue.put(self._replay_item(item))
                self.current = None
                self.started_at = None
                self.paused_at = None
                self.queue.task_done()

    def skip(self) -> None:
        if self.voice and self.voice.is_playing():
            self._was_skipped = True
            self.voice.stop()

    def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            self.paused_at = time.monotonic()
            return True
        return False

    def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            if self.paused_at:
                self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None
            return True
        return False

    def clear_queue(self) -> int:
        """Remove all upcoming tracks while allowing the current track to finish."""
        self.cache.cancel()
        removed = 0
        self.repeat_mode = "off"
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                removed += 1
            except asyncio.QueueEmpty:
                return removed

    def stop(self) -> None:
        self.repeat_mode = "off"
        self.clear_queue()
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()

    def move_queue_item(self, index: int, offset: int) -> int | None:
        """Move an upcoming track by ``offset`` positions without touching playback."""
        target = index + offset
        if index < 0 or target < 0 or index >= self.queue.qsize() or target >= self.queue.qsize():
            return None
        self.cache.cancel()
        items = self.queue._queue
        items[index], items[target] = items[target], items[index]
        return target

    def upcoming(self, limit: int | None = 10) -> list[QueueItem]:
        items = list(self.queue._queue)
        return items if limit is None else items[:limit]

    @property
    def is_paused(self) -> bool:
        return bool(self.voice and self.voice.is_paused())

    @property
    def elapsed(self) -> int:
        if self.started_at is None:
            return 0
        end = self.paused_at if self.paused_at else time.monotonic()
        return max(0, int(end - self.started_at - self.paused_total))

    async def close(self) -> None:
        self.stop()
        self.cache.cancel()
        if self.voice:
            await self.voice.disconnect(force=True)
        self.voice = None
        if self.worker and self.worker is not asyncio.current_task():
            self.worker.cancel()
