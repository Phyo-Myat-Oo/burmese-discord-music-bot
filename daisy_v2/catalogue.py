from __future__ import annotations

import asyncio
import random
import sqlite3
from pathlib import Path

from .database import connect
from .models import Album, Artist, CatalogTrack
from .schema import initialize_catalogue
from .text import clean_album_title, clean_title, extract_artists, search_key


TRACK_SELECT = """
SELECT t.id, t.album_id, a.pcloud_code, t.pcloud_file_id,
       t.display_title AS title,
       COALESCE(NULLIF(t.artist_text,''),
         (SELECT ar.name FROM track_artists ta
          JOIN artists ar ON ar.id=ta.artist_id
          WHERE ta.track_id=t.id ORDER BY ta.position LIMIT 1),
         (SELECT ar.name FROM album_artists aa
          JOIN artists ar ON ar.id=aa.artist_id
          WHERE aa.album_id=a.id ORDER BY aa.position LIMIT 1),
         'Unknown') AS artist,
       a.display_title AS album, a.post_url, p.cover_url, t.duration, t.size,
       t.mediafire_quick_key, a.mediafire_url
FROM tracks t
JOIN albums a ON a.id=t.album_id
LEFT JOIN posts p ON p.url=a.post_url
"""

ALBUM_SELECT = """
SELECT a.id,a.display_title AS title,
       COALESCE((SELECT ar.name FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                 WHERE aa.album_id=a.id ORDER BY aa.position LIMIT 1),'Unknown') AS artist,
       a.post_url,p.cover_url,COUNT(t.id) AS track_count
FROM albums a LEFT JOIN posts p ON p.url=a.post_url LEFT JOIN tracks t ON t.album_id=a.id
"""


class Catalogue:
    """Public Burmese catalogue written by the V2 indexer."""

    def __init__(self, database: Path) -> None:
        self.database = database

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with connect(self.database) as db:
            initialize_catalogue(db)

    async def source_updates(self, urls: list[str]) -> dict[str, str]:
        return await asyncio.to_thread(self._source_updates, urls)

    def _source_updates(self, urls: list[str]) -> dict[str, str]:
        if not urls:
            return {}
        placeholders = ",".join("?" for _ in urls)
        with connect(self.database) as db:
            rows = db.execute(
                f"SELECT url,source_updated FROM posts WHERE url IN ({placeholders})", urls
            ).fetchall()
        return {row["url"]: row["source_updated"] for row in rows}

    async def pcloud_scan_needed(self, codes: list[str]) -> set[str]:
        """Return new or previously failed folders that need another scan."""
        return await asyncio.to_thread(self._pcloud_scan_needed, codes)

    def _pcloud_scan_needed(self, codes: list[str]) -> set[str]:
        unique = list(dict.fromkeys(code for code in codes if code))
        if not unique:
            return set()
        placeholders = ",".join("?" for _ in unique)
        with connect(self.database) as db:
            rows = db.execute(
                f"SELECT pcloud_code,error FROM albums WHERE pcloud_code IN ({placeholders})", unique
            ).fetchall()
        scanned = {row["pcloud_code"] for row in rows if row["error"] is None}
        return set(unique) - scanned

    async def upsert_posts(self, posts: list[dict[str, str]]) -> int:
        return await asyncio.to_thread(self._upsert_posts, posts)

    def _upsert_posts(self, posts: list[dict[str, str]]) -> int:
        if not posts:
            return 0
        with connect(self.database) as db:
            db.executemany("""
                INSERT INTO posts(url,title,published,source_updated,cover_url) VALUES(:url,:title,:published,:source_updated,:cover_url)
                ON CONFLICT(url) DO UPDATE SET title=excluded.title,published=excluded.published,
                source_updated=excluded.source_updated,cover_url=excluded.cover_url
            """, posts)
        return len(posts)

    async def upsert_album(
        self, *, post_url: str, code: str, share_url: str, title: str, scanned_at: str,
        tracks: list[dict] | None, error: str | None = None, mediafire_url: str | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._upsert_album, post_url, code, share_url, title, scanned_at, tracks, error, mediafire_url
        )

    @staticmethod
    def _artist_id(db: sqlite3.Connection, name: str) -> int:
        db.execute(
            "INSERT INTO artists(name,search_key) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET search_key=excluded.search_key",
            (name, search_key(name)),
        )
        return int(db.execute("SELECT id FROM artists WHERE name=?", (name,)).fetchone()[0])

    @classmethod
    def _sync_artists(cls, db: sqlite3.Connection, album_id: int, album_title: str, tracks: list[dict]) -> None:
        db.execute("DELETE FROM album_artists WHERE album_id=?", (album_id,))
        for position, name in enumerate(extract_artists(album_title)):
            artist_id = cls._artist_id(db, name)
            db.execute(
                "INSERT OR IGNORE INTO album_artists(album_id,artist_id,position) VALUES(?,?,?)",
                (album_id, artist_id, position),
            )
        for track in tracks:
            track_id = int(track["id"])
            artist_text = (track.get("artist") or "").strip()
            for position, name in enumerate(extract_artists(f"{artist_text} - track") if artist_text else []):
                artist_id = cls._artist_id(db, name)
                db.execute(
                    "INSERT OR IGNORE INTO track_artists(track_id,artist_id,position) VALUES(?,?,?)",
                    (track_id, artist_id, position),
                )
                db.execute(
                    "INSERT OR IGNORE INTO album_artists(album_id,artist_id,position) VALUES(?,?,?)",
                    (album_id, artist_id, position + 100),
                )

    def _upsert_album(
        self, post_url: str, code: str, share_url: str, title: str, scanned_at: str,
        tracks: list[dict] | None, error: str | None, mediafire_url: str | None,
    ) -> int:
        with connect(self.database) as db:
            db.execute("""
                INSERT INTO albums(
                    post_url,pcloud_code,share_url,title,display_title,search_key,scanned_at,error,mediafire_url
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(pcloud_code) DO UPDATE SET
                    post_url=excluded.post_url,share_url=excluded.share_url,title=excluded.title,
                    display_title=excluded.display_title,search_key=excluded.search_key,
                    scanned_at=excluded.scanned_at,error=excluded.error,mediafire_url=excluded.mediafire_url
            """, (
                post_url, code, share_url, title, clean_album_title(title), search_key(title),
                scanned_at, error, mediafire_url,
            ))
            album_id = int(db.execute("SELECT id FROM albums WHERE pcloud_code=?", (code,)).fetchone()[0])
            if tracks is None:
                return 0
            db.execute("DELETE FROM tracks WHERE album_id=?", (album_id,))
            inserted: list[dict] = []
            for track in tracks:
                cursor = db.execute("""
                    INSERT INTO tracks(
                        album_id,pcloud_file_id,title,display_title,search_key,size,content_type,duration,
                        artist_text,mediafire_quick_key
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    album_id, int(track["file_id"]), track["title"], clean_title(track["title"]),
                    search_key(track["title"]), int(track.get("size") or 0), track.get("content_type"),
                    int(track["duration"]) if track.get("duration") else None,
                    (track.get("artist") or "").strip(), track.get("mediafire_quick_key"),
                ))
                inserted.append({**track, "id": int(cursor.lastrowid)})
            self._sync_artists(db, album_id, title, inserted)
            return len(inserted)

    async def stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._stats)

    def _stats(self) -> dict[str, int]:
        with connect(self.database) as db:
            return {
                "posts": db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
                "albums": db.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
                "tracks": db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
                "artists": db.execute("SELECT COUNT(*) FROM artists").fetchone()[0],
            }

    @staticmethod
    def _track(row: sqlite3.Row) -> CatalogTrack:
        return CatalogTrack(
            id=row["id"], album_id=row["album_id"], pcloud_code=row["pcloud_code"],
            pcloud_file_id=row["pcloud_file_id"], title=row["title"], artist=row["artist"],
            album=row["album"], post_url=row["post_url"], cover_url=row["cover_url"],
            duration=row["duration"], size=row["size"],
            mediafire_quick_key=row["mediafire_quick_key"], mediafire_url=row["mediafire_url"],
        )

    async def search_tracks(self, query: str, *, limit: int = 10, offset: int = 0) -> list[CatalogTrack]:
        return await asyncio.to_thread(self._search_tracks, query, limit, offset)

    def _search_tracks(self, query: str, limit: int, offset: int) -> list[CatalogTrack]:
        key = search_key(query)
        pattern = f"%{key}%"
        with connect(self.database) as db:
            rows = db.execute(TRACK_SELECT + """
                WHERE t.search_key LIKE ? OR a.search_key LIKE ? OR EXISTS (
                    SELECT 1 FROM track_artists ta JOIN artists ar ON ar.id=ta.artist_id
                    WHERE ta.track_id=t.id AND ar.search_key LIKE ?
                ) OR EXISTS (
                    SELECT 1 FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                    WHERE aa.album_id=a.id AND ar.search_key LIKE ?
                )
                ORDER BY t.album_id, t.id LIMIT ? OFFSET ?
            """, (pattern, pattern, pattern, pattern, limit, offset)).fetchall()
        return [self._track(row) for row in rows]

    async def count_tracks(self, query: str) -> int:
        return await asyncio.to_thread(self._count_tracks, query)

    def _count_tracks(self, query: str) -> int:
        key = search_key(query)
        pattern = f"%{key}%"
        with connect(self.database) as db:
            return db.execute("""
                SELECT COUNT(*) FROM tracks t JOIN albums a ON a.id=t.album_id
                WHERE t.search_key LIKE ? OR a.search_key LIKE ? OR EXISTS (
                    SELECT 1 FROM track_artists ta JOIN artists ar ON ar.id=ta.artist_id
                    WHERE ta.track_id=t.id AND ar.search_key LIKE ?
                ) OR EXISTS (
                    SELECT 1 FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                    WHERE aa.album_id=a.id AND ar.search_key LIKE ?
                )
            """, (pattern, pattern, pattern, pattern)).fetchone()[0]

    async def track(self, track_id: int) -> CatalogTrack | None:
        return await asyncio.to_thread(self._track_by_id, track_id)

    def _track_by_id(self, track_id: int) -> CatalogTrack | None:
        with connect(self.database) as db:
            row = db.execute(TRACK_SELECT + " WHERE t.id=?", (track_id,)).fetchone()
        return self._track(row) if row else None

    async def random_track(self, artist: str = "") -> CatalogTrack | None:
        return await asyncio.to_thread(self._random_track, artist)

    def _random_track(self, artist: str) -> CatalogTrack | None:
        key = search_key(artist)
        pattern = f"%{key}%"
        with connect(self.database) as db:
            if key:
                rows = db.execute(TRACK_SELECT + """
                    WHERE EXISTS (
                        SELECT 1 FROM track_artists ta JOIN artists ar ON ar.id=ta.artist_id
                        WHERE ta.track_id=t.id AND ar.search_key LIKE ?
                    ) OR EXISTS (
                        SELECT 1 FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                        WHERE aa.album_id=a.id AND ar.search_key LIKE ?
                    ) LIMIT 1000
                """, (pattern, pattern)).fetchall()
            else:
                rows = db.execute(TRACK_SELECT + " LIMIT 1000").fetchall()
        return self._track(random.choice(rows)) if rows else None

    async def random_album(self, artist: str = "") -> Album | None:
        return await asyncio.to_thread(self._random_album, artist)

    def _random_album(self, artist: str) -> Album | None:
        key = search_key(artist)
        pattern = f"%{key}%"
        with connect(self.database) as db:
            if key:
                rows = db.execute(ALBUM_SELECT + """
                    WHERE EXISTS (
                        SELECT 1 FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                        WHERE aa.album_id=a.id AND ar.search_key LIKE ?
                    ) GROUP BY a.id,a.display_title,a.post_url,p.cover_url LIMIT 1000
                """, (pattern,)).fetchall()
            else:
                rows = db.execute(ALBUM_SELECT + " GROUP BY a.id,a.display_title,a.post_url,p.cover_url LIMIT 1000").fetchall()
        return Album(**dict(random.choice(rows))) if rows else None

    async def artists(self, filter_text: str = "", *, limit: int = 20, offset: int = 0) -> list[Artist]:
        return await asyncio.to_thread(self._artists, filter_text, limit, offset)

    async def count_artists(self, filter_text: str = "") -> int:
        return await asyncio.to_thread(self._count_artists, filter_text)

    def _count_artists(self, filter_text: str) -> int:
        with connect(self.database) as db:
            return db.execute(
                "SELECT COUNT(*) FROM artists WHERE search_key LIKE ?",
                (f"%{search_key(filter_text)}%",),
            ).fetchone()[0]

    def _artists(self, filter_text: str, limit: int, offset: int) -> list[Artist]:
        pattern = f"%{search_key(filter_text)}%"
        with connect(self.database) as db:
            rows = db.execute("""
                SELECT ar.id,ar.name,COUNT(DISTINCT aa.album_id) AS album_count
                FROM artists ar LEFT JOIN album_artists aa ON aa.artist_id=ar.id
                WHERE ar.search_key LIKE ?
                GROUP BY ar.id,ar.name ORDER BY ar.name LIMIT ? OFFSET ?
            """, (pattern, limit, offset)).fetchall()
        return [Artist(row["id"], row["name"], row["album_count"]) for row in rows]

    async def albums_for_artist(self, artist_id: int, *, limit: int = 20, offset: int = 0) -> list[Album]:
        return await asyncio.to_thread(self._albums_for_artist, artist_id, limit, offset)

    async def count_albums_for_artist(self, artist_id: int) -> int:
        return await asyncio.to_thread(self._count_albums_for_artist, artist_id)

    def _count_albums_for_artist(self, artist_id: int) -> int:
        with connect(self.database) as db:
            return db.execute(
                "SELECT COUNT(DISTINCT album_id) FROM album_artists WHERE artist_id=?", (artist_id,)
            ).fetchone()[0]

    def _albums_for_artist(self, artist_id: int, limit: int, offset: int) -> list[Album]:
        with connect(self.database) as db:
            rows = db.execute("""
                SELECT a.id,a.display_title AS title,ar.name AS artist,a.post_url,p.cover_url,
                       COUNT(t.id) AS track_count
                FROM album_artists aa JOIN albums a ON a.id=aa.album_id
                JOIN artists ar ON ar.id=aa.artist_id LEFT JOIN posts p ON p.url=a.post_url
                LEFT JOIN tracks t ON t.album_id=a.id
                WHERE aa.artist_id=? GROUP BY a.id,a.display_title,ar.name,a.post_url,p.cover_url
                ORDER BY a.id DESC LIMIT ? OFFSET ?
            """, (artist_id, limit, offset)).fetchall()
        return [Album(**dict(row)) for row in rows]

    async def album(self, album_id: int) -> Album | None:
        return await asyncio.to_thread(self._album, album_id)

    def _album(self, album_id: int) -> Album | None:
        with connect(self.database) as db:
            row = db.execute(ALBUM_SELECT + " WHERE a.id=? GROUP BY a.id,a.display_title,a.post_url,p.cover_url", (album_id,)).fetchone()
        return Album(**dict(row)) if row else None

    async def search_albums(self, query: str, limit: int = 25) -> list[Album]:
        return await asyncio.to_thread(self._search_albums, query, limit)

    def _search_albums(self, query: str, limit: int) -> list[Album]:
        pattern = f"%{search_key(query)}%"
        with connect(self.database) as db:
            rows = db.execute(ALBUM_SELECT + """
                WHERE a.search_key LIKE ? OR EXISTS (
                    SELECT 1 FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                    WHERE aa.album_id=a.id AND ar.search_key LIKE ?
                )
                GROUP BY a.id,a.display_title,a.post_url,p.cover_url ORDER BY a.id DESC LIMIT ?
            """, (pattern, pattern, limit)).fetchall()
        return [Album(**dict(row)) for row in rows]

    async def album_tracks(self, album_id: int) -> list[CatalogTrack]:
        return await asyncio.to_thread(self._album_tracks, album_id)

    def _album_tracks(self, album_id: int) -> list[CatalogTrack]:
        with connect(self.database) as db:
            rows = db.execute(TRACK_SELECT + " WHERE t.album_id=? ORDER BY t.id", (album_id,)).fetchall()
        return [self._track(row) for row in rows]

    async def autocomplete_tracks(self, query: str, limit: int = 25) -> list[CatalogTrack]:
        return await self.search_tracks(query, limit=limit)

    async def autocomplete_artists(self, query: str, limit: int = 25) -> list[Artist]:
        return await self.artists(query, limit=limit)
