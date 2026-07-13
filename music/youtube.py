from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import yt_dlp


class YouTubeError(RuntimeError):
    pass


@dataclass(frozen=True)
class YouTubeResult:
    title: str
    url: str
    uploader: str
    duration: int | None
    thumbnail: str | None = None

    @property
    def duration_text(self) -> str:
        if self.duration is None:
            return "Unknown length"
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def is_youtube_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    return host in {"youtu.be", "youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"}


class YouTubeClient:
    SEARCH_OPTIONS = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": True,
    }
    STREAM_OPTIONS = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "skip_download": True,
        "noplaylist": True,
    }

    @classmethod
    async def search(cls, query: str, limit: int = 10) -> list[YouTubeResult]:
        return await asyncio.to_thread(cls._search, query, limit)

    @classmethod
    def _search(cls, query: str, limit: int) -> list[YouTubeResult]:
        target = query if is_youtube_url(query) else f"ytsearch{limit}:{query}"
        try:
            with yt_dlp.YoutubeDL(cls.SEARCH_OPTIONS) as ydl:
                info = ydl.extract_info(target, download=False)
        except Exception as exc:
            raise YouTubeError(str(exc)) from exc
        entries = info.get("entries") if info else None
        raw_results = [entry for entry in (entries or [info]) if entry]
        results = []
        for entry in raw_results[:limit]:
            video_id = entry.get("id", "")
            url = entry.get("webpage_url") or entry.get("url", "")
            if not is_youtube_url(url) and video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            if not url:
                continue
            results.append(YouTubeResult(
                title=entry.get("title") or "Untitled YouTube video",
                url=url,
                uploader=entry.get("uploader") or entry.get("channel") or "YouTube",
                duration=int(entry["duration"]) if entry.get("duration") else None,
                thumbnail=entry.get("thumbnail"),
            ))
        return results

    @classmethod
    async def stream_url(cls, video_url: str) -> str:
        return await asyncio.to_thread(cls._stream_url, video_url)

    @classmethod
    def _stream_url(cls, video_url: str) -> str:
        if not is_youtube_url(video_url):
            raise YouTubeError("Only YouTube URLs are accepted")
        try:
            with yt_dlp.YoutubeDL(cls.STREAM_OPTIONS) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception as exc:
            raise YouTubeError(str(exc)) from exc
        stream = info.get("url") if info else None
        if not stream:
            raise YouTubeError("YouTube returned no playable audio stream")
        return stream
