from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from music.player import QueueItem

LOGGER = logging.getLogger(__name__)


async def _http_download(url: str, headers: dict, dest: Path) -> None:
    """Stream an HTTP resource to *dest* using aiohttp."""
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                async for chunk in resp.content.iter_chunked(1 << 17):  # 128 KiB
                    fh.write(chunk)


def _yt_download_sync(opts: dict, url: str) -> None:
    """Blocking yt-dlp download — always run via asyncio.to_thread."""
    import yt_dlp  # noqa: PLC0415
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def _youtube_download(video_url: str, cache_dir: Path) -> Path:
    """Download a YouTube video's best-audio stream; return the local file path."""
    uid = uuid.uuid4().hex
    outtmpl = str(cache_dir / f"pre_{uid}.%(ext)s")
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": outtmpl,
    }
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    await asyncio.to_thread(_yt_download_sync, opts, video_url)
    matches = list(cache_dir.glob(f"pre_{uid}.*"))
    if not matches:
        raise FileNotFoundError(f"yt-dlp produced no output for {video_url}")
    return matches[0]


class StreamCache:
    """
    Pre-download the *next* queued track to disk so playback starts instantly.

    Lifecycle
    ---------
    1. ``await cache.prefetch(next_item)``  — called as soon as the current track
       starts playing.  Kicks off a background download task.
    2. ``path = await cache.consume(next_item)``  — called when the next track is
       dequeued.  Waits for the download to finish and hands over the file path.
    3. FFmpeg plays from the local file instead of a remote URL.
    4. ``StreamCache.delete_file(path)``  — called in the finally block to remove the
       temp file after playback ends.

    On skip / stop / queue-reorder the cache is cancelled and any partial file is
    deleted via ``cache.cancel()``.
    """

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._task: asyncio.Task[None] | None = None
        self._item: object | None = None   # identity key — the QueueItem object itself
        self._file: Path | None = None
        self._failed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def prefetch(self, item: QueueItem) -> None:
        """Cancel any existing prefetch and start downloading *item* in the background."""
        self.cancel()
        self._item = item
        self._failed = False
        self._task = asyncio.create_task(self._download(item), name="stream-prefetch")

    async def consume(self, item: QueueItem) -> Path | None:
        """
        Wait for the prefetch of *item* to complete and return the local file path.

        Returns ``None`` if *item* was not prefetched, the download failed, or
        the downloaded file is unexpectedly missing.
        """
        if self._item is not item:
            return None
        if self._task is not None and not self._task.done():
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                return None
        if self._failed or self._file is None or not self._file.exists():
            return None
        # Hand ownership to the caller; clear our reference so cancel() won't delete it.
        result, self._file = self._file, None
        self._item = None
        self._task = None
        return result

    def cancel(self) -> None:
        """Cancel any in-flight download and remove any partial file."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._wipe_file()
        self._task = None
        self._item = None
        self._failed = False

    @staticmethod
    def delete_file(path: Path) -> None:
        """Remove a consumed prefetch file once playback has finished."""
        try:
            path.unlink(missing_ok=True)
            LOGGER.debug("Deleted prefetch file %s", path)
        except OSError as exc:
            LOGGER.debug("Could not delete prefetch file %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _download(self, item: QueueItem) -> None:
        try:
            LOGGER.info("Prefetching '%s' (%s) ...", item.title, item.source_type)

            if item.source_type == "youtube" and item.source_url:
                # Use yt-dlp to download the best audio format directly.
                self._file = await _youtube_download(item.source_url, self._dir)

            else:
                # Resolve the stream URL, then download via HTTP (pCloud / generic).
                resolved = await item.source() if callable(item.source) else item.source

                if isinstance(resolved, Path):
                    # Already a local file — nothing to download.
                    self._file = resolved
                    return

                if isinstance(resolved, dict):
                    url: str = resolved["url"]
                    req_headers: dict = resolved.get("headers", {})
                else:
                    url = str(resolved)
                    req_headers = {}

                uid = uuid.uuid4().hex
                dest = self._dir / f"pre_{uid}.audio"
                await _http_download(url, req_headers, dest)
                self._file = dest

            LOGGER.info("Prefetch done  '%s' -> %s", item.title, self._file)

        except asyncio.CancelledError:
            self._wipe_file()
            raise
        except Exception:
            LOGGER.exception("Prefetch failed for '%s'", item.title)
            self._wipe_file()
            self._failed = True

    def _wipe_file(self) -> None:
        """Delete and clear ``self._file`` if it exists."""
        if self._file is not None:
            try:
                self._file.unlink(missing_ok=True)
            except OSError:
                pass
            self._file = None
