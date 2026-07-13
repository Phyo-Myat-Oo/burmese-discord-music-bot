from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

from .text import clean_album_title, clean_track_title, search_key


class Library:
    def __init__(self, database: Path, music_dir: Path) -> None:
        self.database = database
        self.music_dir = music_dir

    async def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.music_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS posts (
                    url TEXT PRIMARY KEY, title TEXT NOT NULL, published TEXT,
                    content TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                    source_updated TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY, post_url TEXT, title TEXT NOT NULL,
                    path TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS pcloud_albums (
                    id INTEGER PRIMARY KEY, post_url TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE, share_url TEXT NOT NULL,
                    title TEXT NOT NULL, scanned_at TEXT NOT NULL, error TEXT,
                    display_title TEXT, search_key TEXT
                );
                CREATE TABLE IF NOT EXISTS pcloud_tracks (
                    id INTEGER PRIMARY KEY, album_id INTEGER NOT NULL,
                    file_id INTEGER NOT NULL, title TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, content_type TEXT,
                    display_title TEXT, search_key TEXT,
                    FOREIGN KEY(album_id) REFERENCES pcloud_albums(id) ON DELETE CASCADE,
                    UNIQUE(album_id, file_id)
                );
                CREATE TABLE IF NOT EXISTS favorites (
                    user_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id,track_id),
                    FOREIGN KEY(track_id) REFERENCES pcloud_tracks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_posts_title ON posts(title);
                CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
                CREATE INDEX IF NOT EXISTS idx_pcloud_tracks_title ON pcloud_tracks(title);
                CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id,created_at DESC);
            """)
            self._ensure_column(db, "posts", "source_updated", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "pcloud_albums", "display_title", "TEXT")
            self._ensure_column(db, "pcloud_albums", "search_key", "TEXT")
            self._ensure_column(db, "pcloud_tracks", "display_title", "TEXT")
            self._ensure_column(db, "pcloud_tracks", "search_key", "TEXT")
            self._backfill_search_fields(db)
            db.execute("CREATE INDEX IF NOT EXISTS idx_pcloud_tracks_search ON pcloud_tracks(search_key)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_pcloud_albums_search ON pcloud_albums(search_key)")

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _backfill_search_fields(db: sqlite3.Connection) -> None:
        albums = db.execute(
            "SELECT id,title FROM pcloud_albums WHERE display_title IS NULL OR search_key IS NULL"
        ).fetchall()
        db.executemany(
            "UPDATE pcloud_albums SET display_title=?,search_key=? WHERE id=?",
            ((clean_album_title(row["title"]), search_key(row["title"]), row["id"]) for row in albums),
        )
        tracks = db.execute(
            "SELECT id,title FROM pcloud_tracks WHERE display_title IS NULL OR search_key IS NULL"
        ).fetchall()
        db.executemany(
            "UPDATE pcloud_tracks SET display_title=?,search_key=? WHERE id=?",
            ((clean_track_title(row["title"]), search_key(row["title"]), row["id"]) for row in tracks),
        )

    async def upsert_posts(self, posts: list[dict[str, str]]) -> int:
        return await asyncio.to_thread(self._upsert_posts, posts)

    def _upsert_posts(self, posts: list[dict[str, str]]) -> int:
        with closing(self._connect()) as db, db:
            db.executemany("""
                INSERT INTO posts(url,title,published,content,updated_at,source_updated)
                VALUES(:url,:title,:published,:content,:updated_at,:source_updated)
                ON CONFLICT(url) DO UPDATE SET title=excluded.title,
                published=excluded.published, content=excluded.content,
                updated_at=excluded.updated_at, source_updated=excluded.source_updated
            """, posts)
        return len(posts)

    async def search_posts(self, query: str, limit: int = 10):
        return await asyncio.to_thread(self._search_posts, query, limit)

    def _search_posts(self, query: str, limit: int):
        with closing(self._connect()) as db, db:
            return db.execute(
                "SELECT title,url FROM posts WHERE title LIKE ? OR content LIKE ? ORDER BY published DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()

    async def find_track(self, query: str):
        return await asyncio.to_thread(self._find_track, query)

    async def scan_local_tracks(self) -> int:
        return await asyncio.to_thread(self._scan_local_tracks)

    def _scan_local_tracks(self) -> int:
        extensions = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav"}
        files = [p for p in self.music_dir.rglob("*") if p.is_file() and p.suffix.lower() in extensions]
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT INTO tracks(title,path) VALUES(?,?) ON CONFLICT(path) DO UPDATE SET title=excluded.title",
                ((path.stem, str(path.resolve())) for path in files),
            )
        return len(files)

    def _find_track(self, query: str):
        with closing(self._connect()) as db, db:
            return db.execute(
                "SELECT title,path FROM tracks WHERE title LIKE ? ORDER BY title LIMIT 1", (f"%{query}%",)
            ).fetchone()

    async def upsert_pcloud_album(
        self, post_url: str, code: str, share_url: str, title: str,
        scanned_at: str, tracks: list[dict], error: str | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._upsert_pcloud_album, post_url, code, share_url, title, scanned_at, tracks, error
        )

    async def upsert_pending_pcloud_links(self, links: list[tuple[str, str, str, str]], scanned_at: str) -> int:
        return await asyncio.to_thread(self._upsert_pending_pcloud_links, links, scanned_at)

    def _upsert_pending_pcloud_links(self, links, scanned_at) -> int:
        unique = list(dict.fromkeys(links))
        with closing(self._connect()) as db, db:
            db.executemany("""
                INSERT INTO pcloud_albums(post_url,title,share_url,code,scanned_at,error)
                VALUES(?,?,?,?,?,'pending') ON CONFLICT(code) DO UPDATE SET
                post_url=excluded.post_url, title=excluded.title,
                share_url=excluded.share_url
            """, ((post_url, title, share_url, code, scanned_at) for post_url, title, share_url, code in unique))
        return len({item[3] for item in unique})

    def _upsert_pcloud_album(self, post_url, code, share_url, title, scanned_at, tracks, error) -> int:
        with closing(self._connect()) as db, db:
            db.execute("""
                INSERT INTO pcloud_albums(
                    post_url,code,share_url,title,scanned_at,error,display_title,search_key
                ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET
                post_url=excluded.post_url, share_url=excluded.share_url,
                title=excluded.title, scanned_at=excluded.scanned_at, error=excluded.error,
                display_title=excluded.display_title,search_key=excluded.search_key
            """, (
                post_url, code, share_url, title, scanned_at, error,
                clean_album_title(title), search_key(title),
            ))
            album_id = db.execute("SELECT id FROM pcloud_albums WHERE code=?", (code,)).fetchone()["id"]
            if error is None:
                db.executemany("""
                    INSERT INTO pcloud_tracks(
                        album_id,file_id,title,size,content_type,display_title,search_key
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(album_id,file_id) DO UPDATE SET
                        title=excluded.title,size=excluded.size,
                        content_type=excluded.content_type,
                        display_title=excluded.display_title,search_key=excluded.search_key
                """, ((
                    album_id, x["file_id"], x["title"], x["size"], x["content_type"],
                    clean_track_title(x["title"]), search_key(x["title"]),
                ) for x in tracks))
                file_ids = [x["file_id"] for x in tracks]
                if file_ids:
                    placeholders = ",".join("?" for _ in file_ids)
                    db.execute(
                        f"DELETE FROM pcloud_tracks WHERE album_id=? AND file_id NOT IN ({placeholders})",
                        (album_id, *file_ids),
                    )
                else:
                    db.execute("DELETE FROM pcloud_tracks WHERE album_id=?", (album_id,))
        return len(tracks)

    async def find_pcloud_track(self, query: str):
        return await asyncio.to_thread(self._find_pcloud_track, query)

    def _find_pcloud_track(self, query: str):
        key = search_key(query)
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT t.id,COALESCE(t.display_title,t.title) AS title,t.file_id,a.code,
                       COALESCE(a.display_title,a.title) AS album_title
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                WHERE t.search_key LIKE ? OR a.search_key LIKE ?
                ORDER BY CASE WHEN t.search_key=? THEN 0 WHEN t.search_key LIKE ? THEN 1 ELSE 2 END,
                         t.display_title LIMIT 1
            """, (f"%{key}%", f"%{key}%", key, f"{key}%")).fetchone()

    async def search_pcloud_tracks(self, query: str, limit: int = 10, offset: int = 0):
        return await asyncio.to_thread(self._search_pcloud_tracks, query, limit, offset)

    def _search_pcloud_tracks(self, query: str, limit: int, offset: int):
        key = search_key(query)
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT t.id,COALESCE(t.display_title,t.title) AS title,
                       COALESCE(a.display_title,a.title) AS album_title
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                WHERE t.search_key LIKE ? OR a.search_key LIKE ?
                ORDER BY CASE
                    WHEN t.search_key = ? THEN 0
                    WHEN t.search_key LIKE ? THEN 1
                    WHEN t.search_key LIKE ? THEN 2
                    ELSE 3 END,
                    a.display_title,t.display_title LIMIT ? OFFSET ?
            """, (
                f"%{key}%", f"%{key}%", key, f"{key}%", f"%{key}%", limit, offset
            )).fetchall()

    async def count_pcloud_tracks(self, query: str) -> int:
        return await asyncio.to_thread(self._count_pcloud_tracks, query)

    def _count_pcloud_tracks(self, query: str) -> int:
        key = search_key(query)
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT COUNT(1) FROM pcloud_tracks t
                JOIN pcloud_albums a ON a.id=t.album_id
                WHERE t.search_key LIKE ? OR a.search_key LIKE ?
            """, (f"%{key}%", f"%{key}%")).fetchone()[0]

    async def pcloud_track_by_id(self, track_id: int):
        return await asyncio.to_thread(self._pcloud_track_by_id, track_id)

    def _pcloud_track_by_id(self, track_id: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT t.id,COALESCE(t.display_title,t.title) AS title,t.file_id,a.code,
                       COALESCE(a.display_title,a.title) AS album_title
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                WHERE t.id=?
            """, (track_id,)).fetchone()

    async def count_artists(self, filter_text: str = "") -> int:
        return await asyncio.to_thread(self._count_artists, filter_text)

    def _count_artists(self, filter_text: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT COUNT(1) FROM (
                    SELECT DISTINCT TRIM(SUBSTR(display_title,1,INSTR(display_title,' - ')-1)) AS artist
                    FROM pcloud_albums
                    WHERE error IS NULL AND INSTR(display_title,' - ') > 1
                      AND TRIM(SUBSTR(display_title,1,INSTR(display_title,' - ')-1)) LIKE ?
                )
            """, (f"%{filter_text}%",)).fetchone()[0]

    async def list_artists(self, filter_text: str = "", limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_artists, filter_text, limit, offset)

    def _list_artists(self, filter_text: str, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT artist,COUNT(1) AS album_count FROM (
                    SELECT TRIM(SUBSTR(display_title,1,INSTR(display_title,' - ')-1)) AS artist
                    FROM pcloud_albums
                    WHERE error IS NULL AND INSTR(display_title,' - ') > 1
                )
                WHERE artist LIKE ?
                GROUP BY artist ORDER BY artist LIMIT ? OFFSET ?
            """, (f"%{filter_text}%", limit, offset)).fetchall()

    async def count_albums_by_artist(self, artist: str) -> int:
        return await asyncio.to_thread(self._count_albums_by_artist, artist)

    def _count_albums_by_artist(self, artist: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT COUNT(1) FROM pcloud_albums
                WHERE error IS NULL AND INSTR(display_title,' - ') > 1
                  AND TRIM(SUBSTR(display_title,1,INSTR(display_title,' - ')-1)) = ?
            """, (artist,)).fetchone()[0]

    async def list_albums_by_artist(self, artist: str, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_albums_by_artist, artist, limit, offset)

    def _list_albums_by_artist(self, artist: str, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT a.id,a.display_title AS title,COUNT(t.id) AS track_count
                FROM pcloud_albums a LEFT JOIN pcloud_tracks t ON t.album_id=a.id
                WHERE a.error IS NULL AND INSTR(a.display_title,' - ') > 1
                  AND TRIM(SUBSTR(a.display_title,1,INSTR(a.display_title,' - ')-1)) = ?
                GROUP BY a.id ORDER BY a.display_title LIMIT ? OFFSET ?
            """, (artist, limit, offset)).fetchall()

    async def count_album_tracks(self, album_id: int) -> int:
        return await asyncio.to_thread(self._count_album_tracks, album_id)

    def _count_album_tracks(self, album_id: int) -> int:
        with closing(self._connect()) as db, db:
            return db.execute(
                "SELECT COUNT(1) FROM pcloud_tracks WHERE album_id=?", (album_id,)
            ).fetchone()[0]

    async def list_album_tracks(self, album_id: int, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_album_tracks, album_id, limit, offset)

    def _list_album_tracks(self, album_id: int, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT t.id,t.display_title AS title,a.display_title AS album_title
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                WHERE t.album_id=? ORDER BY t.file_id LIMIT ? OFFSET ?
            """, (album_id, limit, offset)).fetchall()

    async def album_by_id(self, album_id: int):
        return await asyncio.to_thread(self._album_by_id, album_id)

    def _album_by_id(self, album_id: int):
        with closing(self._connect()) as db, db:
            return db.execute(
                "SELECT id,display_title AS title FROM pcloud_albums WHERE id=?", (album_id,)
            ).fetchone()

    async def add_favorite(self, user_id: int, track_id: int) -> bool:
        return await asyncio.to_thread(self._add_favorite, str(user_id), track_id)

    def _add_favorite(self, user_id: str, track_id: int) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO favorites(user_id,track_id) VALUES(?,?)",
                (user_id, track_id),
            )
            return cursor.rowcount > 0

    async def remove_favorite(self, user_id: int, track_id: int) -> bool:
        return await asyncio.to_thread(self._remove_favorite, str(user_id), track_id)

    def _remove_favorite(self, user_id: str, track_id: int) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "DELETE FROM favorites WHERE user_id=? AND track_id=?", (user_id, track_id)
            )
            return cursor.rowcount > 0

    async def count_favorites(self, user_id: int) -> int:
        return await asyncio.to_thread(self._count_favorites, str(user_id))

    def _count_favorites(self, user_id: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute(
                "SELECT COUNT(1) FROM favorites WHERE user_id=?", (user_id,)
            ).fetchone()[0]

    async def list_favorites(self, user_id: int, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_favorites, str(user_id), limit, offset)

    def _list_favorites(self, user_id: str, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT t.id,t.display_title AS title,a.display_title AS album_title
                FROM favorites f JOIN pcloud_tracks t ON t.id=f.track_id
                JOIN pcloud_albums a ON a.id=t.album_id
                WHERE f.user_id=? ORDER BY f.created_at DESC LIMIT ? OFFSET ?
            """, (user_id, limit, offset)).fetchall()

    async def random_track(self, artist: str = ""):
        return await asyncio.to_thread(self._random_track, artist)

    def _random_track(self, artist: str):
        with closing(self._connect()) as db, db:
            params: tuple = ()
            condition = "a.error IS NULL"
            if artist:
                condition += " AND TRIM(SUBSTR(a.display_title,1,INSTR(a.display_title,' - ')-1)) LIKE ?"
                params = (f"%{artist}%",)
            return db.execute(f"""
                SELECT t.id,t.display_title AS title,t.file_id,a.code,a.display_title AS album_title
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                WHERE {condition} ORDER BY RANDOM() LIMIT 1
            """, params).fetchone()

    async def random_album(self, artist: str = ""):
        return await asyncio.to_thread(self._random_album, artist)

    def _random_album(self, artist: str):
        with closing(self._connect()) as db, db:
            params: tuple = ()
            condition = "a.error IS NULL"
            if artist:
                condition += " AND TRIM(SUBSTR(a.display_title,1,INSTR(a.display_title,' - ')-1)) LIKE ?"
                params = (f"%{artist}%",)
            return db.execute(f"""
                SELECT a.id,a.display_title AS title,COUNT(t.id) AS track_count
                FROM pcloud_albums a JOIN pcloud_tracks t ON t.album_id=a.id
                WHERE {condition} GROUP BY a.id HAVING COUNT(t.id)>0
                ORDER BY RANDOM() LIMIT 1
            """, params).fetchone()

    async def album_playback_tracks(self, album_id: int, limit: int = 100):
        return await asyncio.to_thread(self._album_playback_tracks, album_id, limit)

    def _album_playback_tracks(self, album_id: int, limit: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT t.id,t.display_title AS title,t.file_id,a.code,a.display_title AS album_title
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                WHERE t.album_id=? ORDER BY t.file_id LIMIT ?
            """, (album_id, limit)).fetchall()

    async def catalogue_stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._catalogue_stats)

    def _catalogue_stats(self) -> dict[str, int]:
        with closing(self._connect()) as db, db:
            return {
                "posts": db.execute("SELECT COUNT(1) FROM posts").fetchone()[0],
                "albums": db.execute("SELECT COUNT(1) FROM pcloud_albums WHERE error IS NULL").fetchone()[0],
                "tracks": db.execute("SELECT COUNT(1) FROM pcloud_tracks").fetchone()[0],
                "failed": db.execute("SELECT COUNT(1) FROM pcloud_albums WHERE error IS NOT NULL").fetchone()[0],
                "favorites": db.execute("SELECT COUNT(1) FROM favorites").fetchone()[0],
            }

    async def source_updates(self, urls: list[str]) -> dict[str, str]:
        return await asyncio.to_thread(self._source_updates, urls)

    def _source_updates(self, urls: list[str]) -> dict[str, str]:
        if not urls:
            return {}
        placeholders = ",".join("?" for _ in urls)
        with closing(self._connect()) as db, db:
            rows = db.execute(
                f"SELECT url,source_updated FROM posts WHERE url IN ({placeholders})", urls
            ).fetchall()
            return {row["url"]: row["source_updated"] for row in rows}

    async def pending_pcloud_albums(self, include_failed: bool = False, limit: int | None = None):
        return await asyncio.to_thread(self._pending_pcloud_albums, include_failed, limit)

    def _pending_pcloud_albums(self, include_failed: bool, limit: int | None):
        condition = "error IS NOT NULL" if include_failed else "error = 'pending'"
        sql = f"SELECT post_url,code,share_url,title FROM pcloud_albums WHERE {condition} ORDER BY id"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with closing(self._connect()) as db, db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]
