from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from .models import QueueTrack
from .sources import MediaSources


LOGGER = logging.getLogger(__name__)


class CacheError(RuntimeError):
    pass


class DiskCache:
    """Bounded, validated, temporary audio cache with one download per track."""

    def __init__(
        self,
        directory: Path,
        sources: MediaSources,
        *,
        max_bytes: int,
        max_track_bytes: int,
        ttl_seconds: int,
        ffmpeg_path: str | None = None,
    ) -> None:
        self.directory = directory
        self.sources = sources
        self.max_bytes = max_bytes
        self.max_track_bytes = max_track_bytes
        self.ttl_seconds = ttl_seconds
        self.ffmpeg_path = ffmpeg_path
        self._inflight: dict[str, asyncio.Task[Path]] = {}
        self._pinned: set[str] = set()
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._cleanup_orphans)
        await self.evict()

    def _cleanup_orphans(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for path in self.directory.glob("*"):
            if path.suffix == ".part" or path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def _path(self, track: QueueTrack) -> Path:
        return self.directory / self.sources.stable_file_name(track.source_key)

    async def ensure(self, track: QueueTrack) -> Path:
        """Return a validated local copy, downloading once even under concurrent requests."""
        target = self._path(track)
        if await asyncio.to_thread(self._valid, target):
            os.utime(target, None)
            return target
        async with self._lock:
            existing = self._inflight.get(track.source_key)
            if existing is None:
                existing = asyncio.create_task(self._download(track, target), name=f"cache:{track.source_key}")
                self._inflight[track.source_key] = existing
                existing.add_done_callback(
                    lambda done, key=track.source_key: self._discard_completed(key, done)
                )
        try:
            return await asyncio.shield(existing)
        finally:
            if existing.done():
                async with self._lock:
                    self._inflight.pop(track.source_key, None)

    def _discard_completed(self, key: str, task: asyncio.Task[Path]) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def prefetch(self, tracks: list[QueueTrack]) -> None:
        """Start bounded background downloads; callers never wait for these tasks."""
        for track in tracks:
            if track.source_key in self._inflight or await asyncio.to_thread(self._valid, self._path(track)):
                continue
            task = asyncio.create_task(self.ensure(track), name=f"prefetch:{track.source_key}")
            task.add_done_callback(self._log_prefetch_result)

    @staticmethod
    def _log_prefetch_result(task: asyncio.Task[Path]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            LOGGER.warning("Track prefetch failed: %s", exc)

    async def _download(self, track: QueueTrack, target: Path) -> Path:
        partial = target.with_suffix(".part")
        partial.unlink(missing_ok=True)
        LOGGER.info("Buffering track locally: %s", track.title)
        try:
            await self.sources.download(track, partial, self.max_track_bytes)
            if not await asyncio.to_thread(self._valid, partial):
                raise CacheError("Downloaded audio did not pass validation")
            os.replace(partial, target)
            await self.evict(exclude={track.source_key})
            LOGGER.info("Track buffered: %s", track.title)
            return target
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def _valid(self, path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return False
            ffprobe = self._find_ffprobe()
            if not ffprobe:
                return True
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1", str(path)],
                capture_output=True, text=True, timeout=20,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _find_ffprobe(self) -> str | None:
        if self.ffmpeg_path:
            candidate = Path(self.ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if candidate.is_file():
                return str(candidate)
        return shutil.which("ffprobe")

    def pin(self, track: QueueTrack) -> None:
        self._pinned.add(track.source_key)

    def unpin(self, track: QueueTrack) -> None:
        self._pinned.discard(track.source_key)

    async def evict(self, *, exclude: set[str] | None = None) -> None:
        await asyncio.to_thread(self._evict, exclude or set())

    def _evict(self, exclude: set[str]) -> None:
        protected = {
            self.sources.stable_file_name(key).removesuffix(".media")
            for key in (self._pinned | exclude)
        }
        candidates: list[tuple[float, Path, str]] = []
        total = 0
        cutoff = time.time() - self.ttl_seconds
        for path in self.directory.glob("*.media"):
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            key = path.stem
            if stat.st_mtime < cutoff and key not in protected:
                path.unlink(missing_ok=True)
                total -= stat.st_size
            else:
                candidates.append((stat.st_mtime, path, key))
        for _, path, key in sorted(candidates):
            if total <= self.max_bytes:
                break
            if key in protected:
                continue
            try:
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
            except OSError:
                continue
