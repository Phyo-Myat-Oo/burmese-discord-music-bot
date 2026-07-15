from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlparse

import aiohttp
import yt_dlp

from .models import QueueTrack, SourceType, YouTubeTrack


PCLOUD_HOSTS = ("https://api.pcloud.com", "https://eapi.pcloud.com")
MEDIAFIRE_API = "https://www.mediafire.com/api/1.4"
LOGGER = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


class MediaSources:
    """Fetch fresh pCloud media or cache a YouTube track without persisting URLs."""

    def __init__(self, cookies_file: Path | None = None) -> None:
        self.cookies_file = cookies_file

    async def search_youtube(self, query: str, limit: int = 10) -> list[YouTubeTrack]:
        return await asyncio.to_thread(self._search_youtube_sync, query, limit)

    def _search_youtube_sync(self, query: str, limit: int) -> list[YouTubeTrack]:
        host = (urlparse(query).hostname or "").lower()
        target = query if host in {"youtu.be", "youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"} else f"ytsearch{limit}:{query}"
        options = self._youtube_options("-")
        options.update({"skip_download": True, "extract_flat": "in_playlist"})
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(target, download=False)
        except Exception as exc:
            raise SourceError(f"YouTube search failed: {exc}") from exc
        raw = (info or {}).get("entries") or [info]
        results: list[YouTubeTrack] = []
        for entry in raw:
            if not entry:
                continue
            video_id = entry.get("id", "")
            url = entry.get("webpage_url") or entry.get("url") or ""
            if video_id and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={video_id}"
            if not url:
                continue
            results.append(YouTubeTrack(
                url=url,
                title=entry.get("title") or "Untitled YouTube video",
                uploader=entry.get("uploader") or entry.get("channel") or "YouTube",
                duration=int(entry["duration"]) if entry.get("duration") else None,
                thumbnail=entry.get("thumbnail"),
            ))
        return results[:limit]

    async def _pcloud_call(self, method: str, params: dict[str, str | int]) -> dict:
        last_error = "pCloud request failed"
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for host in PCLOUD_HOSTS:
                async with session.get(f"{host}/{method}", params=params) as response:
                    response.raise_for_status()
                    payload = json.loads((await response.read()).decode("utf-8", errors="replace"))
                if payload.get("result") == 0:
                    return payload
                last_error = str(payload.get("error") or last_error)
                if payload.get("result") != 7001:
                    break
        raise SourceError(last_error)

    async def pcloud_url(self, code: str, file_id: int) -> str:
        payload = await self._pcloud_call(
            "getpublinkdownload", {"code": code, "fileid": file_id, "forcedownload": 0}
        )
        hosts, path = payload.get("hosts", []), payload.get("path")
        if not hosts or not path:
            raise SourceError("pCloud returned no downloadable audio URL")
        return f"https://{hosts[0]}{quote(path, safe='/:%@?&=+$,;~()*!')}"

    async def download(self, track: QueueTrack, destination: Path, max_bytes: int) -> None:
        if track.source_type is SourceType.PCLOUD:
            if not track.pcloud_code or track.pcloud_file_id is None:
                raise SourceError("pCloud queue item is missing its stable file identity")
            try:
                if track.pcloud_code.startswith("mediafire") and track.mediafire_quick_key:
                    await self._download_mediafire(track.mediafire_quick_key, destination, max_bytes)
                else:
                    await self._download_pcloud(track.pcloud_code, track.pcloud_file_id, destination, max_bytes)
            except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as pcloud_error:
                if not track.mediafire_quick_key or track.pcloud_code.startswith("mediafire"):
                    raise
                LOGGER.warning(
                    "pCloud failed for %s; trying its MediaFire fallback: %s", track.title, pcloud_error
                )
                try:
                    await self._download_mediafire(track.mediafire_quick_key, destination, max_bytes)
                except SourceError as mediafire_error:
                    raise SourceError(
                        f"pCloud failed ({pcloud_error}); MediaFire fallback failed ({mediafire_error})"
                    ) from mediafire_error
            return
        if track.source_type is SourceType.YOUTUBE:
            if not track.youtube_url:
                raise SourceError("YouTube queue item is missing its video URL")
            await self._download_youtube(track.youtube_url, destination, max_bytes)
            return
        raise SourceError(f"Unsupported source: {track.source_type}")

    async def _download_pcloud(self, code: str, file_id: int, destination: Path, max_bytes: int) -> None:
        url = await self.pcloud_url(code, file_id)
        timeout = aiohttp.ClientTimeout(total=600, sock_read=60)
        destination.parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    advertised = int(response.headers.get("Content-Length", "0") or 0)
                    if advertised > max_bytes:
                        raise SourceError(f"Track is larger than the {max_bytes // (1024 * 1024)} MB cache limit")
                    with destination.open("wb") as target:
                        async for chunk in response.content.iter_chunked(256 * 1024):
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                raise SourceError(f"Track exceeded the {max_bytes // (1024 * 1024)} MB cache limit")
                            target.write(chunk)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        if downloaded == 0:
            destination.unlink(missing_ok=True)
            raise SourceError("pCloud returned an empty audio file")

    async def _mediafire_download_url(self, quick_key: str) -> str:
        timeout = aiohttp.ClientTimeout(total=45)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{MEDIAFIRE_API}/file/get_links.php",
                    params={"quick_key": quick_key},
                    headers={"User-Agent": "DaisyV2/0.1"},
                ) as response:
                    response.raise_for_status()
                    root = ET.fromstring(await response.read())
        except (aiohttp.ClientError, asyncio.TimeoutError, ET.ParseError) as exc:
            raise SourceError("MediaFire link lookup failed") from exc
        link = root.findtext(".//normal_download")
        if not link:
            message = root.findtext(".//direct_download_error_message") or root.findtext(".//message")
            raise SourceError(message or "MediaFire returned no download link")
        return link

    async def _download_mediafire(self, quick_key: str, destination: Path, max_bytes: int) -> None:
        url = await self._mediafire_download_url(quick_key)
        timeout = aiohttp.ClientTimeout(total=600, sock_read=60)
        destination.parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"User-Agent": "DaisyV2/0.1"}) as response:
                    response.raise_for_status()
                    if response.content_type == "text/html":
                        raise SourceError("MediaFire returned an HTML download page")
                    advertised = int(response.headers.get("Content-Length", "0") or 0)
                    if advertised > max_bytes:
                        raise SourceError(
                            f"MediaFire track is larger than the {max_bytes // (1024 * 1024)} MB cache limit"
                        )
                    with destination.open("wb") as target:
                        async for chunk in response.content.iter_chunked(256 * 1024):
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                raise SourceError(
                                    f"MediaFire track exceeded the {max_bytes // (1024 * 1024)} MB cache limit"
                                )
                            target.write(chunk)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        if downloaded == 0:
            destination.unlink(missing_ok=True)
            raise SourceError("MediaFire returned an empty audio file")

    def _youtube_options(self, output_template: str, *, use_cookies: bool = True) -> dict:
        options: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": output_template,
        }
        if use_cookies and self.cookies_file and self.cookies_file.is_file():
            options["cookiefile"] = str(self.cookies_file)
        return options

    async def _download_youtube(self, url: str, destination: Path, max_bytes: int) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host not in {"youtu.be", "youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"}:
            raise SourceError("Only YouTube URLs are allowed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._youtube_download_sync, url, destination, max_bytes)

    def _youtube_download_sync(self, url: str, destination: Path, max_bytes: int) -> None:
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix="youtube-") as temporary:
            temp = Path(temporary)
            attempts = [True, False] if self.cookies_file and self.cookies_file.is_file() else [False]
            last_error: Exception | None = None
            files: list[Path] = []
            for use_cookies in attempts:
                for path in temp.iterdir():
                    if path.is_file():
                        path.unlink(missing_ok=True)
                options = self._youtube_options(str(temp / "audio.%(ext)s"), use_cookies=use_cookies)
                options["max_filesize"] = max_bytes
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        ydl.download([url])
                    files = [path for path in temp.iterdir() if path.is_file()]
                    if files:
                        break
                    last_error = SourceError("YouTube download produced no audio file")
                except Exception as exc:
                    last_error = exc
                    if use_cookies:
                        LOGGER.warning("YouTube cookie extraction failed; retrying without cookies: %s", exc)
            if not files:
                raise SourceError(f"YouTube download failed: {last_error}") from last_error
            source = max(files, key=lambda path: path.stat().st_size)
            if source.stat().st_size == 0 or source.stat().st_size > max_bytes:
                raise SourceError("YouTube audio is empty or exceeds the configured cache limit")
            os.replace(source, destination)

    @staticmethod
    def stable_file_name(source_key: str) -> str:
        return hashlib.sha256(source_key.encode("utf-8")).hexdigest() + ".media"
