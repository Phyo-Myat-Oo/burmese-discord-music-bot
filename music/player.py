from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import discord

LOGGER = logging.getLogger(__name__)


@dataclass
class QueueItem:
    source: Path | Callable[[], Awaitable[str]]
    title: str
    album: str = ""
    requester: str = "Unknown"
    track_id: int | None = None
    source_type: str = "pcloud"
    source_url: str = ""
    uploader: str = ""
    duration: int | None = None
    thumbnail: str | None = None


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


class GuildPlayer:
    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.voice: discord.VoiceClient | None = None
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.ffmpeg = find_ffmpeg()
        self.current: QueueItem | None = None

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
        self, resolver: Callable[[], Awaitable[str]], title: str,
        album: str = "", requester: str = "Unknown", track_id: int | None = None,
        source_type: str = "pcloud", source_url: str = "", uploader: str = "",
        duration: int | None = None, thumbnail: str | None = None,
    ) -> int:
        await self.queue.put(QueueItem(
            resolver, title, album, requester, track_id, source_type,
            source_url, uploader, duration, thumbnail,
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
            try:
                finished = asyncio.Event()
                loop = asyncio.get_running_loop()
                source_value = await item.source() if callable(item.source) else str(item.source)
                source = discord.FFmpegOpusAudio(
                    source_value,
                    executable=self.ffmpeg,
                    before_options="-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    options="-vn",
                )

                def after(error: Exception | None) -> None:
                    if error:
                        LOGGER.error("Voice playback failed for %s: %s", item.title, error)
                    loop.call_soon_threadsafe(finished.set)

                self.voice.play(source, after=after)
                await finished.wait()
            except Exception:
                LOGGER.exception("Could not start playback for %s", item.title)
            finally:
                self.current = None
                self.queue.task_done()

    def skip(self) -> None:
        if self.voice and self.voice.is_playing():
            self.voice.stop()

    def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            return True
        return False

    def stop(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()

    def upcoming(self, limit: int = 10) -> list[QueueItem]:
        return list(self.queue._queue)[:limit]

    @property
    def is_paused(self) -> bool:
        return bool(self.voice and self.voice.is_paused())

    async def close(self) -> None:
        self.stop()
        if self.voice:
            await self.voice.disconnect(force=True)
        self.voice = None
        if self.worker and self.worker is not asyncio.current_task():
            self.worker.cancel()
