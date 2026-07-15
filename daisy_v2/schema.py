from __future__ import annotations

import sqlite3


CATALOG_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    published TEXT,
    source_updated TEXT NOT NULL DEFAULT '',
    cover_url TEXT
);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY,
    post_url TEXT NOT NULL REFERENCES posts(url) ON DELETE CASCADE,
    pcloud_code TEXT NOT NULL UNIQUE,
    share_url TEXT NOT NULL,
    title TEXT NOT NULL,
    display_title TEXT NOT NULL,
    search_key TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    error TEXT,
    mediafire_url TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    pcloud_file_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    display_title TEXT NOT NULL,
    search_key TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    content_type TEXT,
    duration INTEGER,
    artist_text TEXT NOT NULL DEFAULT '',
    mediafire_quick_key TEXT,
    UNIQUE(album_id, pcloud_file_id)
);

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    search_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS album_artists (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(album_id, artist_id)
);

CREATE TABLE IF NOT EXISTS track_artists (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(track_id, artist_id)
);

CREATE INDEX IF NOT EXISTS idx_albums_search ON albums(search_key);
CREATE INDEX IF NOT EXISTS idx_tracks_search ON tracks(search_key);
CREATE INDEX IF NOT EXISTS idx_artists_search ON artists(search_key);
CREATE INDEX IF NOT EXISTS idx_album_artists_artist ON album_artists(artist_id, album_id);
CREATE INDEX IF NOT EXISTS idx_track_artists_artist ON track_artists(artist_id, track_id);
"""


STATE_SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('pcloud', 'youtube')),
    source_key TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    duration INTEGER,
    thumbnail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, source_type, source_key)
);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('personal', 'server')),
    owner_user_id TEXT,
    guild_id TEXT,
    name TEXT NOT NULL COLLATE NOCASE CHECK(length(name) BETWEEN 1 AND 100),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK((scope='personal' AND owner_user_id IS NOT NULL AND guild_id IS NULL) OR
          (scope='server' AND guild_id IS NOT NULL AND owner_user_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_personal_playlist_name
ON playlists(owner_user_id, name) WHERE scope='personal';
CREATE UNIQUE INDEX IF NOT EXISTS idx_server_playlist_name
ON playlists(guild_id, name) WHERE scope='server';

CREATE TABLE IF NOT EXISTS playlist_items (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK(source_type IN ('pcloud', 'youtube')),
    source_key TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    duration INTEGER,
    thumbnail TEXT,
    position INTEGER NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(playlist_id, position)
);
CREATE INDEX IF NOT EXISTS idx_playlist_items_order ON playlist_items(playlist_id, position);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('pcloud', 'youtube')),
    source_key TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    duration INTEGER,
    completed INTEGER NOT NULL DEFAULT 0,
    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_history_guild_date ON play_history(guild_id, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_user_date ON play_history(user_id, played_at DESC);
"""


def initialize_catalogue(connection: sqlite3.Connection) -> None:
    connection.executescript(CATALOG_SCHEMA)
    # V2.1 adds optional MediaFire fallback metadata without touching existing
    # pCloud rows or requiring users to delete their catalogue database.
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(albums)").fetchall()
    }
    if "mediafire_url" not in columns:
        connection.execute("ALTER TABLE albums ADD COLUMN mediafire_url TEXT")
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(tracks)").fetchall()
    }
    if "mediafire_quick_key" not in columns:
        connection.execute("ALTER TABLE tracks ADD COLUMN mediafire_quick_key TEXT")
    connection.execute(
        "INSERT INTO schema_meta(key,value) VALUES('schema_version','2') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )


def initialize_state(connection: sqlite3.Connection) -> None:
    connection.executescript(STATE_SCHEMA)
    connection.execute(
        "INSERT INTO schema_meta(key,value) VALUES('schema_version','1') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
