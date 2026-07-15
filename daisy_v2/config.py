from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser()


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: int | None
    admin_role: str
    ffmpeg_path: str | None
    data_dir: Path
    catalog_db: Path
    state_db: Path
    cache_dir: Path
    cache_max_bytes: int
    cache_max_track_bytes: int
    cache_ttl_seconds: int
    prefetch_tracks: int
    playback_idle_seconds: int
    sync_interval_seconds: int
    youtube_cookies_file: Path | None

    @property
    def is_configured(self) -> bool:
        return bool(self.token)


def load_settings() -> Settings:
    """Read configuration without creating any files or directories."""
    load_dotenv()
    data_dir = _path("DAISY_V2_DATA_DIR", Path("data") / "daisy_v2")
    catalog_db = _path("DAISY_V2_CATALOG_DB", data_dir / "catalog.db")
    state_db = _path("DAISY_V2_STATE_DB", data_dir / "state.db")
    cache_dir = _path("DAISY_V2_CACHE_DIR", data_dir / "cache")
    guild_text = os.getenv("DISCORD_GUILD_ID", "").strip()
    try:
        guild_id = int(guild_text) if guild_text else None
    except ValueError:
        guild_id = None
    cookie_text = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    return Settings(
        token=os.getenv("DISCORD_TOKEN", "").strip(),
        guild_id=guild_id,
        admin_role=os.getenv("MUSIC_ADMIN_ROLE", "DJ").strip() or "DJ",
        ffmpeg_path=os.getenv("FFMPEG_PATH", "").strip() or None,
        data_dir=data_dir,
        catalog_db=catalog_db,
        state_db=state_db,
        cache_dir=cache_dir,
        cache_max_bytes=_positive_int("CACHE_MAX_MB", 350) * 1024 * 1024,
        cache_max_track_bytes=_positive_int("CACHE_MAX_TRACK_MB", 120) * 1024 * 1024,
        cache_ttl_seconds=_positive_int("CACHE_TTL_HOURS", 24) * 60 * 60,
        prefetch_tracks=_positive_int("PREFETCH_TRACKS", 1, minimum=0),
        playback_idle_seconds=_positive_int("PLAYBACK_IDLE_SECONDS", 300, minimum=30),
        sync_interval_seconds=_positive_int("SYNC_INTERVAL_HOURS", 6) * 60 * 60,
        youtube_cookies_file=Path(cookie_text).expanduser() if cookie_text else None,
    )
