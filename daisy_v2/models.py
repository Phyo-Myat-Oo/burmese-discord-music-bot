from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceType(StrEnum):
    PCLOUD = "pcloud"
    YOUTUBE = "youtube"


class RepeatMode(StrEnum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


@dataclass(frozen=True)
class CatalogTrack:
    id: int
    album_id: int
    pcloud_code: str
    pcloud_file_id: int
    title: str
    artist: str
    album: str
    post_url: str
    cover_url: str | None
    duration: int | None
    size: int
    mediafire_quick_key: str | None = None
    mediafire_url: str | None = None

    @property
    def cache_key(self) -> str:
        if self.mediafire_quick_key and self.pcloud_code.startswith("mediafire"):
            return f"mediafire:{self.mediafire_quick_key}"
        return f"pcloud:{self.pcloud_code}:{self.pcloud_file_id}"


@dataclass(frozen=True)
class Album:
    id: int
    title: str
    artist: str
    post_url: str
    cover_url: str | None
    track_count: int = 0


@dataclass(frozen=True)
class Artist:
    id: int
    name: str
    album_count: int


@dataclass(frozen=True)
class YouTubeTrack:
    url: str
    title: str
    uploader: str
    duration: int | None
    thumbnail: str | None

    @property
    def cache_key(self) -> str:
        return f"youtube:{self.url}"


@dataclass(frozen=True)
class QueueTrack:
    source_type: SourceType
    source_key: str
    title: str
    artist: str
    album: str
    requester_id: int
    requester_name: str
    duration: int | None = None
    source_url: str = ""
    cover_url: str | None = None
    catalog_track_id: int | None = None
    pcloud_code: str | None = None
    pcloud_file_id: int | None = None
    youtube_url: str | None = None
    uploader: str = ""
    mediafire_quick_key: str | None = None
    mediafire_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedRemote:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
