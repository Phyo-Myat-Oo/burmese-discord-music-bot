from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

from .text import clean_album_title, clean_track_title, extract_artists, search_key


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
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS posts (
                    url TEXT PRIMARY KEY, title TEXT NOT NULL, published TEXT,
                    content TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                    source_updated TEXT NOT NULL DEFAULT '', cover_url TEXT
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY, post_url TEXT, title TEXT NOT NULL,
                    path TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS pcloud_albums (
                    id INTEGER PRIMARY KEY, post_url TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE, share_url TEXT NOT NULL,
                    title TEXT NOT NULL, scanned_at TEXT NOT NULL, error TEXT,
                    display_title TEXT, search_key TEXT, metadata_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS pcloud_tracks (
                    id INTEGER PRIMARY KEY, album_id INTEGER NOT NULL,
                    file_id INTEGER NOT NULL, title TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, content_type TEXT,
                    display_title TEXT, search_key TEXT, duration INTEGER, artist_text TEXT,
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
                CREATE TABLE IF NOT EXISTS youtube_favorites (
                    user_id TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    uploader TEXT NOT NULL,
                    duration INTEGER,
                    thumbnail TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id,video_url)
                );
                CREATE TABLE IF NOT EXISTS artists (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    search_key TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS album_artists (
                    album_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(album_id,artist_id),
                    FOREIGN KEY(album_id) REFERENCES pcloud_albums(id) ON DELETE CASCADE,
                    FOREIGN KEY(artist_id) REFERENCES artists(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS track_artists (
                    track_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(track_id,artist_id),
                    FOREIGN KEY(track_id) REFERENCES pcloud_tracks(id) ON DELETE CASCADE,
                    FOREIGN KEY(artist_id) REFERENCES artists(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY,
                    scope TEXT NOT NULL CHECK(scope IN ('personal','server')),
                    owner_user_id TEXT,
                    guild_id TEXT,
                    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK((scope='personal' AND owner_user_id IS NOT NULL) OR
                          (scope='server' AND guild_id IS NOT NULL))
                );
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY,
                    playlist_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL CHECK(source_type IN ('pcloud','youtube')),
                    track_id INTEGER,
                    source_url TEXT,
                    title TEXT NOT NULL,
                    artist TEXT,
                    album TEXT,
                    uploader TEXT,
                    duration INTEGER,
                    thumbnail TEXT,
                    position INTEGER NOT NULL,
                    added_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                    FOREIGN KEY(track_id) REFERENCES pcloud_tracks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS play_history (
                    id INTEGER PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source_type TEXT NOT NULL CHECK(source_type IN ('pcloud','youtube')),
                    track_id INTEGER,
                    source_url TEXT,
                    title TEXT NOT NULL,
                    artist TEXT,
                    album TEXT,
                    duration INTEGER,
                    completed INTEGER NOT NULL DEFAULT 0,
                    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(track_id) REFERENCES pcloud_tracks(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_posts_title ON posts(title);
                CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
                CREATE INDEX IF NOT EXISTS idx_pcloud_tracks_title ON pcloud_tracks(title);
                CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_youtube_favorites_user
                    ON youtube_favorites(user_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_artists_search ON artists(search_key);
                CREATE INDEX IF NOT EXISTS idx_album_artists_artist ON album_artists(artist_id,album_id);
                CREATE INDEX IF NOT EXISTS idx_track_artists_artist ON track_artists(artist_id,track_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_personal_playlist_name
                    ON playlists(owner_user_id,name) WHERE scope='personal';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_server_playlist_name
                    ON playlists(guild_id,name) WHERE scope='server';
                CREATE INDEX IF NOT EXISTS idx_playlist_items_order
                    ON playlist_items(playlist_id,position);
                CREATE INDEX IF NOT EXISTS idx_history_guild_date
                    ON play_history(guild_id,played_at DESC);
                CREATE INDEX IF NOT EXISTS idx_history_user_date
                    ON play_history(user_id,played_at DESC);
            """)
            self._ensure_column(db, "posts", "source_updated", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "posts", "cover_url", "TEXT")
            self._ensure_column(db, "pcloud_albums", "display_title", "TEXT")
            self._ensure_column(db, "pcloud_albums", "search_key", "TEXT")
            self._ensure_column(db, "pcloud_albums", "metadata_version", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "pcloud_tracks", "display_title", "TEXT")
            self._ensure_column(db, "pcloud_tracks", "search_key", "TEXT")
            self._ensure_column(db, "pcloud_tracks", "duration", "INTEGER")
            self._ensure_column(db, "pcloud_tracks", "artist_text", "TEXT")
            self._backfill_search_fields(db)
            self._backfill_artists(db)
            db.execute("""
                UPDATE pcloud_albums SET metadata_version=1
                WHERE metadata_version=0 AND EXISTS (
                    SELECT 1 FROM pcloud_tracks t
                    WHERE t.album_id=pcloud_albums.id AND length(t.artist_text)>0
                )
            """)
            db.execute(
                "INSERT INTO schema_meta(key,value) VALUES('version','3') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
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

    @staticmethod
    def _sync_album_artists(db: sqlite3.Connection, album_id: int, title: str) -> None:
        db.execute("DELETE FROM album_artists WHERE album_id=?", (album_id,))
        for position, name in enumerate(extract_artists(title)):
            db.execute(
                "INSERT INTO artists(name,search_key) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET search_key=excluded.search_key",
                (name, search_key(name)),
            )
            artist_id = db.execute("SELECT id FROM artists WHERE name=?", (name,)).fetchone()["id"]
            db.execute(
                "INSERT OR IGNORE INTO album_artists(album_id,artist_id,position) VALUES(?,?,?)",
                (album_id, artist_id, position),
            )

    @classmethod
    def _backfill_artists(cls, db: sqlite3.Connection) -> None:
        rows = db.execute("""
            SELECT a.id,a.title FROM pcloud_albums a
            WHERE NOT EXISTS (SELECT 1 FROM album_artists aa WHERE aa.album_id=a.id)
        """).fetchall()
        for row in rows:
            cls._sync_album_artists(db, row["id"], row["title"])

    @staticmethod
    def _sync_track_artist(db: sqlite3.Connection, track_id: int, album_id: int, artist_text: str) -> None:
        db.execute("DELETE FROM track_artists WHERE track_id=?", (track_id,))
        names = [part.strip() for part in extract_artists(f"{artist_text} - album")] if artist_text else []
        for position, name in enumerate(names):
            db.execute(
                "INSERT INTO artists(name,search_key) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET search_key=excluded.search_key",
                (name, search_key(name)),
            )
            artist_id = db.execute("SELECT id FROM artists WHERE name=?", (name,)).fetchone()["id"]
            db.execute(
                "INSERT OR IGNORE INTO track_artists(track_id,artist_id,position) VALUES(?,?,?)",
                (track_id, artist_id, position),
            )
            db.execute(
                "INSERT OR IGNORE INTO album_artists(album_id,artist_id,position) VALUES(?,?,?)",
                (album_id, artist_id, position + 100),
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
                INSERT INTO posts(url,title,published,content,updated_at,source_updated,cover_url)
                VALUES(:url,:title,:published,:content,:updated_at,:source_updated,:cover_url)
                ON CONFLICT(url) DO UPDATE SET title=excluded.title,
                published=excluded.published, content=excluded.content,
                updated_at=excluded.updated_at, source_updated=excluded.source_updated,
                cover_url=excluded.cover_url
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
                    post_url,code,share_url,title,scanned_at,error,display_title,search_key,metadata_version
                ) VALUES(?,?,?,?,?,?,?,?,1) ON CONFLICT(code) DO UPDATE SET
                post_url=excluded.post_url, share_url=excluded.share_url,
                title=excluded.title, scanned_at=excluded.scanned_at, error=excluded.error,
                display_title=excluded.display_title,search_key=excluded.search_key,
                metadata_version=1
            """, (
                post_url, code, share_url, title, scanned_at, error,
                clean_album_title(title), search_key(title),
            ))
            album_id = db.execute("SELECT id FROM pcloud_albums WHERE code=?", (code,)).fetchone()["id"]
            self._sync_album_artists(db, album_id, title)
            if error is None:
                db.executemany("""
                    INSERT INTO pcloud_tracks(
                        album_id,file_id,title,size,content_type,display_title,search_key,
                        duration,artist_text
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(album_id,file_id) DO UPDATE SET
                        title=excluded.title,size=excluded.size,
                        content_type=excluded.content_type,
                        display_title=excluded.display_title,search_key=excluded.search_key,
                        duration=excluded.duration,artist_text=excluded.artist_text
                """, ((
                    album_id, x["file_id"], x["title"], x["size"], x["content_type"],
                    clean_track_title(x["title"]), search_key(x["title"]), x.get("duration"),
                    x.get("artist", ""),
                ) for x in tracks))
                artist_ids: dict[str, int] = {}
                track_artist_rows = []
                album_artist_rows = []
                for item in tracks:
                    track_id = db.execute(
                        "SELECT id FROM pcloud_tracks WHERE album_id=? AND file_id=?",
                        (album_id, item["file_id"]),
                    ).fetchone()["id"]
                    db.execute("DELETE FROM track_artists WHERE track_id=?", (track_id,))
                    names = (
                        extract_artists(f"{item.get('artist', '')} - album")
                        if item.get("artist") else []
                    )
                    for position, name in enumerate(names):
                        if name not in artist_ids:
                            db.execute(
                                "INSERT INTO artists(name,search_key) VALUES(?,?) "
                                "ON CONFLICT(name) DO UPDATE SET search_key=excluded.search_key",
                                (name, search_key(name)),
                            )
                            artist_ids[name] = db.execute(
                                "SELECT id FROM artists WHERE name=?", (name,)
                            ).fetchone()["id"]
                        artist_id = artist_ids[name]
                        track_artist_rows.append((track_id, artist_id, position))
                        album_artist_rows.append((album_id, artist_id, position + 100))
                db.executemany(
                    "INSERT OR IGNORE INTO track_artists(track_id,artist_id,position) VALUES(?,?,?)",
                    track_artist_rows,
                )
                db.executemany(
                    "INSERT OR IGNORE INTO album_artists(album_id,artist_id,position) VALUES(?,?,?)",
                    album_artist_rows,
                )
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
                       COALESCE(a.display_title,a.title) AS album_title,t.duration,
                       a.post_url,p.cover_url,
                       COALESCE(NULLIF(t.artist_text,''),GROUP_CONCAT(ar.name,'၊ ')) AS artist
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                LEFT JOIN posts p ON p.url=a.post_url
                LEFT JOIN album_artists aa ON aa.album_id=a.id
                LEFT JOIN artists ar ON ar.id=aa.artist_id
                WHERE t.search_key LIKE ? OR a.search_key LIKE ?
                GROUP BY t.id
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
                       COALESCE(a.display_title,a.title) AS album_title,t.duration,
                       a.post_url,p.cover_url,
                       COALESCE(NULLIF(t.artist_text,''),GROUP_CONCAT(ar.name,'၊ ')) AS artist
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                LEFT JOIN posts p ON p.url=a.post_url
                LEFT JOIN album_artists aa ON aa.album_id=a.id
                LEFT JOIN artists ar ON ar.id=aa.artist_id
                WHERE t.id=?
                GROUP BY t.id
            """, (track_id,)).fetchone()

    async def count_artists(self, filter_text: str = "") -> int:
        return await asyncio.to_thread(self._count_artists, filter_text)

    def _count_artists(self, filter_text: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT COUNT(1) FROM (
                    SELECT name AS artist FROM artists WHERE name LIKE ?
                )
            """, (f"%{filter_text}%",)).fetchone()[0]

    async def list_artists(self, filter_text: str = "", limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_artists, filter_text, limit, offset)

    def _list_artists(self, filter_text: str, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT ar.name AS artist,COUNT(aa.album_id) AS album_count
                FROM artists ar JOIN album_artists aa ON aa.artist_id=ar.id
                JOIN pcloud_albums a ON a.id=aa.album_id
                WHERE ar.name LIKE ? AND a.error IS NULL
                GROUP BY ar.id ORDER BY ar.name LIMIT ? OFFSET ?
            """, (f"%{filter_text}%", limit, offset)).fetchall()

    async def count_albums_by_artist(self, artist: str) -> int:
        return await asyncio.to_thread(self._count_albums_by_artist, artist)

    def _count_albums_by_artist(self, artist: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT COUNT(1) FROM album_artists aa JOIN artists ar ON ar.id=aa.artist_id
                JOIN pcloud_albums a ON a.id=aa.album_id
                WHERE a.error IS NULL AND ar.name=?
            """, (artist,)).fetchone()[0]

    async def list_albums_by_artist(self, artist: str, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_albums_by_artist, artist, limit, offset)

    def _list_albums_by_artist(self, artist: str, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT a.id,a.display_title AS title,COUNT(t.id) AS track_count
                FROM pcloud_albums a JOIN album_artists aa ON aa.album_id=a.id
                JOIN artists ar ON ar.id=aa.artist_id
                LEFT JOIN pcloud_tracks t ON t.album_id=a.id
                WHERE a.error IS NULL AND ar.name=?
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
                SELECT t.id,t.display_title AS title,a.display_title AS album_title,t.duration
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

    async def add_youtube_favorite(
        self, user_id: int, video_url: str, title: str, uploader: str,
        duration: int | None, thumbnail: str | None,
    ) -> bool:
        return await asyncio.to_thread(
            self._add_youtube_favorite, str(user_id), video_url, title,
            uploader, duration, thumbnail,
        )

    def _add_youtube_favorite(
        self, user_id: str, video_url: str, title: str, uploader: str,
        duration: int | None, thumbnail: str | None,
    ) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute("""
                INSERT OR IGNORE INTO youtube_favorites(
                    user_id,video_url,title,uploader,duration,thumbnail
                ) VALUES(?,?,?,?,?,?)
            """, (user_id, video_url, title, uploader, duration, thumbnail))
            return cursor.rowcount > 0

    async def remove_youtube_favorite(self, user_id: int, video_url: str) -> bool:
        return await asyncio.to_thread(self._remove_youtube_favorite, str(user_id), video_url)

    def _remove_youtube_favorite(self, user_id: str, video_url: str) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "DELETE FROM youtube_favorites WHERE user_id=? AND video_url=?",
                (user_id, video_url),
            )
            return cursor.rowcount > 0

    async def count_favorites(self, user_id: int) -> int:
        return await asyncio.to_thread(self._count_favorites, str(user_id))

    def _count_favorites(self, user_id: str) -> int:
        with closing(self._connect()) as db, db:
            pcloud = db.execute(
                "SELECT COUNT(1) FROM favorites WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            youtube = db.execute(
                "SELECT COUNT(1) FROM youtube_favorites WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            return pcloud + youtube

    async def list_favorites(self, user_id: int, limit: int = 20, offset: int = 0):
        return await asyncio.to_thread(self._list_favorites, str(user_id), limit, offset)

    def _list_favorites(self, user_id: str, limit: int, offset: int):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT * FROM (
                    SELECT 'pcloud' AS source_type,CAST(t.id AS TEXT) AS source_id,
                           t.display_title AS title,a.display_title AS album_title,
                           NULL AS source_url,NULL AS uploader,NULL AS duration,
                           NULL AS thumbnail,f.created_at
                    FROM favorites f JOIN pcloud_tracks t ON t.id=f.track_id
                    JOIN pcloud_albums a ON a.id=t.album_id WHERE f.user_id=?
                    UNION ALL
                    SELECT 'youtube',video_url,title,'YouTube • ' || uploader,
                           video_url,uploader,duration,thumbnail,created_at
                    FROM youtube_favorites WHERE user_id=?
                ) ORDER BY created_at DESC LIMIT ? OFFSET ?
            """, (user_id, user_id, limit, offset)).fetchall()

    async def random_track(self, artist: str = ""):
        return await asyncio.to_thread(self._random_track, artist)

    def _random_track(self, artist: str):
        with closing(self._connect()) as db, db:
            params: tuple = ()
            condition = "a.error IS NULL"
            if artist:
                condition += " AND ar.name LIKE ?"
                params = (f"%{artist}%",)
            return db.execute(f"""
                SELECT t.id,t.display_title AS title,t.file_id,a.code,a.display_title AS album_title,
                       t.duration,a.post_url,p.cover_url,
                       COALESCE(NULLIF(t.artist_text,''),GROUP_CONCAT(ar.name,'၊ ')) AS artist
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                LEFT JOIN posts p ON p.url=a.post_url
                LEFT JOIN album_artists aa ON aa.album_id=a.id
                LEFT JOIN artists ar ON ar.id=aa.artist_id
                WHERE {condition} GROUP BY t.id ORDER BY RANDOM() LIMIT 1
            """, params).fetchone()

    async def random_album(self, artist: str = ""):
        return await asyncio.to_thread(self._random_album, artist)

    def _random_album(self, artist: str):
        with closing(self._connect()) as db, db:
            params: tuple = ()
            condition = "a.error IS NULL"
            if artist:
                condition += " AND EXISTS (SELECT 1 FROM album_artists aa2 "
                condition += "JOIN artists ar2 ON ar2.id=aa2.artist_id "
                condition += "WHERE aa2.album_id=a.id AND ar2.name LIKE ?)"
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
                SELECT t.id,t.display_title AS title,t.file_id,a.code,a.display_title AS album_title,
                       t.duration,a.post_url,p.cover_url,
                       COALESCE(NULLIF(t.artist_text,''),GROUP_CONCAT(ar.name,'၊ ')) AS artist
                FROM pcloud_tracks t JOIN pcloud_albums a ON a.id=t.album_id
                LEFT JOIN posts p ON p.url=a.post_url
                LEFT JOIN album_artists aa ON aa.album_id=a.id
                LEFT JOIN artists ar ON ar.id=aa.artist_id
                WHERE t.album_id=? GROUP BY t.id ORDER BY t.file_id LIMIT ?
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
                "favorites": (
                    db.execute("SELECT COUNT(1) FROM favorites").fetchone()[0]
                    + db.execute("SELECT COUNT(1) FROM youtube_favorites").fetchone()[0]
                ),
                "playlists": db.execute("SELECT COUNT(1) FROM playlists").fetchone()[0],
                "history": db.execute("SELECT COUNT(1) FROM play_history").fetchone()[0],
                "artists": db.execute("SELECT COUNT(1) FROM artists").fetchone()[0],
                "durations": db.execute(
                    "SELECT COUNT(1) FROM pcloud_tracks WHERE duration IS NOT NULL"
                ).fetchone()[0],
            }

    async def create_playlist(
        self, scope: str, name: str, user_id: int, guild_id: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._create_playlist, scope, name.strip(), str(user_id), str(guild_id)
        )

    def _create_playlist(self, scope: str, name: str, user_id: str, guild_id: str) -> bool:
        try:
            with closing(self._connect()) as db, db:
                db.execute(
                    "INSERT INTO playlists(scope,owner_user_id,guild_id,name) VALUES(?,?,?,?)",
                    (scope, user_id if scope == "personal" else None,
                     guild_id if scope == "server" else None, name),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def _playlist(self, db, scope: str, name: str, user_id: str, guild_id: str):
        if scope == "personal":
            return db.execute(
                "SELECT * FROM playlists WHERE scope='personal' AND owner_user_id=? AND name=?",
                (user_id, name),
            ).fetchone()
        return db.execute(
            "SELECT * FROM playlists WHERE scope='server' AND guild_id=? AND name=?",
            (guild_id, name),
        ).fetchone()

    async def list_playlists(self, user_id: int, guild_id: int):
        return await asyncio.to_thread(self._list_playlists, str(user_id), str(guild_id))

    def _list_playlists(self, user_id: str, guild_id: str):
        with closing(self._connect()) as db, db:
            return db.execute("""
                SELECT p.id,p.scope,p.name,COUNT(i.id) AS item_count
                FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id=p.id
                WHERE (p.scope='personal' AND p.owner_user_id=?)
                   OR (p.scope='server' AND p.guild_id=?)
                GROUP BY p.id ORDER BY p.scope,p.name
            """, (user_id, guild_id)).fetchall()

    async def add_playlist_item(
        self, scope: str, name: str, user_id: int, guild_id: int, item: dict,
    ) -> bool:
        return await asyncio.to_thread(
            self._add_playlist_item, scope, name, str(user_id), str(guild_id), item
        )

    def _add_playlist_item(self, scope, name, user_id, guild_id, item) -> bool:
        with closing(self._connect()) as db, db:
            playlist = self._playlist(db, scope, name, user_id, guild_id)
            if not playlist:
                return False
            position = db.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM playlist_items WHERE playlist_id=?",
                (playlist["id"],),
            ).fetchone()[0]
            db.execute("""
                INSERT INTO playlist_items(
                    playlist_id,source_type,track_id,source_url,title,artist,album,
                    uploader,duration,thumbnail,position,added_by
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                playlist["id"], item["source_type"], item.get("track_id"),
                item.get("source_url"), item["title"], item.get("artist"),
                item.get("album"), item.get("uploader"), item.get("duration"),
                item.get("thumbnail"), position, user_id,
            ))
            return True

    async def remove_playlist_item(
        self, scope: str, name: str, position: int, user_id: int, guild_id: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._remove_playlist_item, scope, name, position, str(user_id), str(guild_id)
        )

    def _remove_playlist_item(self, scope, name, position, user_id, guild_id) -> bool:
        with closing(self._connect()) as db, db:
            playlist = self._playlist(db, scope, name, user_id, guild_id)
            if not playlist:
                return False
            cursor = db.execute(
                "DELETE FROM playlist_items WHERE playlist_id=? AND position=?",
                (playlist["id"], position),
            )
            if cursor.rowcount:
                rows = db.execute(
                    "SELECT id FROM playlist_items WHERE playlist_id=? ORDER BY position",
                    (playlist["id"],),
                ).fetchall()
                db.executemany(
                    "UPDATE playlist_items SET position=? WHERE id=?",
                    ((index, row["id"]) for index, row in enumerate(rows, 1)),
                )
            return cursor.rowcount > 0

    async def playlist_items(self, scope: str, name: str, user_id: int, guild_id: int):
        return await asyncio.to_thread(
            self._playlist_items, scope, name, str(user_id), str(guild_id)
        )

    def _playlist_items(self, scope, name, user_id, guild_id):
        with closing(self._connect()) as db, db:
            playlist = self._playlist(db, scope, name, user_id, guild_id)
            if not playlist:
                return []
            return db.execute("""
                SELECT i.*,t.file_id,a.code,a.post_url,p.cover_url,
                       COALESCE(GROUP_CONCAT(ar.name,'၊ '),i.artist) AS resolved_artist,
                       COALESCE(a.display_title,i.album) AS resolved_album,
                       COALESCE(t.display_title,i.title) AS resolved_title,
                       COALESCE(t.duration,i.duration) AS resolved_duration
                FROM playlist_items i
                LEFT JOIN pcloud_tracks t ON t.id=i.track_id
                LEFT JOIN pcloud_albums a ON a.id=t.album_id
                LEFT JOIN posts p ON p.url=a.post_url
                LEFT JOIN album_artists aa ON aa.album_id=a.id
                LEFT JOIN artists ar ON ar.id=aa.artist_id
                WHERE i.playlist_id=? GROUP BY i.id ORDER BY i.position
            """, (playlist["id"],)).fetchall()

    async def record_play_start(self, guild_id: int, user_id: int, item: dict) -> int:
        return await asyncio.to_thread(
            self._record_play_start, str(guild_id), str(user_id), item
        )

    def _record_play_start(self, guild_id: str, user_id: str, item: dict) -> int:
        with closing(self._connect()) as db, db:
            cursor = db.execute("""
                INSERT INTO play_history(
                    guild_id,user_id,source_type,track_id,source_url,title,
                    artist,album,duration
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                guild_id, user_id, item["source_type"], item.get("track_id"),
                item.get("source_url"), item["title"], item.get("artist"),
                item.get("album"), item.get("duration"),
            ))
            return cursor.lastrowid

    async def complete_history(self, history_id: int) -> None:
        await asyncio.to_thread(self._complete_history, history_id)

    def _complete_history(self, history_id: int) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE play_history SET completed=1 WHERE id=?", (history_id,))

    async def update_track_duration(self, track_id: int, duration: int) -> None:
        await asyncio.to_thread(self._update_track_duration, track_id, duration)

    def _update_track_duration(self, track_id: int, duration: int) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE pcloud_tracks SET duration=? WHERE id=?", (duration, track_id))

    async def history(self, guild_id: int, user_id: int | None = None, limit: int = 15):
        return await asyncio.to_thread(self._history, str(guild_id), str(user_id) if user_id else None, limit)

    def _history(self, guild_id: str, user_id: str | None, limit: int):
        with closing(self._connect()) as db, db:
            if user_id:
                return db.execute("""
                    SELECT * FROM play_history WHERE guild_id=? AND user_id=?
                    ORDER BY played_at DESC LIMIT ?
                """, (guild_id, user_id, limit)).fetchall()
            return db.execute("""
                SELECT * FROM play_history WHERE guild_id=?
                ORDER BY played_at DESC LIMIT ?
            """, (guild_id, limit)).fetchall()

    async def top_history(self, guild_id: int, field: str, limit: int = 10):
        return await asyncio.to_thread(self._top_history, str(guild_id), field, limit)

    def _top_history(self, guild_id: str, field: str, limit: int):
        column = "artist" if field == "artist" else "title"
        with closing(self._connect()) as db, db:
            return db.execute(f"""
                SELECT {column} AS name,COUNT(1) AS plays FROM play_history
                WHERE guild_id=? AND {column} IS NOT NULL AND {column}!=''
                GROUP BY {column} ORDER BY plays DESC,name LIMIT ?
            """, (guild_id, limit)).fetchall()

    async def user_stats(self, guild_id: int, user_id: int):
        return await asyncio.to_thread(self._user_stats, str(guild_id), str(user_id))

    def _user_stats(self, guild_id: str, user_id: str):
        with closing(self._connect()) as db, db:
            summary = db.execute("""
                SELECT COUNT(1) AS plays,COALESCE(SUM(duration),0) AS seconds
                FROM play_history WHERE guild_id=? AND user_id=?
            """, (guild_id, user_id)).fetchone()
            favorite = db.execute("""
                SELECT artist,COUNT(1) AS plays FROM play_history
                WHERE guild_id=? AND user_id=? AND artist IS NOT NULL
                GROUP BY artist ORDER BY plays DESC LIMIT 1
            """, (guild_id, user_id)).fetchone()
            return {"plays": summary["plays"], "seconds": summary["seconds"],
                    "top_artist": favorite["artist"] if favorite else "None yet"}

    async def autocomplete_tracks(self, query: str, limit: int = 25):
        return await self.search_pcloud_tracks(query, limit, 0)

    async def autocomplete_artists(self, query: str, limit: int = 25):
        return await self.list_artists(query, limit, 0)

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

    async def all_pcloud_albums(self, limit: int | None = None):
        return await asyncio.to_thread(self._all_pcloud_albums, limit)

    def _all_pcloud_albums(self, limit: int | None):
        sql = "SELECT post_url,code,share_url,title FROM pcloud_albums WHERE error IS NULL ORDER BY id"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with closing(self._connect()) as db, db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    async def pcloud_albums_needing_refresh(self, limit: int | None = None):
        return await asyncio.to_thread(self._pcloud_albums_needing_refresh, limit)

    def _pcloud_albums_needing_refresh(self, limit: int | None):
        sql = """
            SELECT post_url,code,share_url,title FROM pcloud_albums
            WHERE error IS NULL AND metadata_version<1 ORDER BY id
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with closing(self._connect()) as db, db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]
