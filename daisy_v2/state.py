from __future__ import annotations

import asyncio
from pathlib import Path

from .database import connect
from .models import QueueTrack, SourceType
from .schema import initialize_state


class UserState:
    """Private Discord user data stored separately from public catalogue data."""

    def __init__(self, database: Path) -> None:
        self.database = database

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with connect(self.database) as db:
            initialize_state(db)

    @staticmethod
    def _snapshot(track: QueueTrack) -> tuple[str | int | None, ...]:
        return (
            track.source_type.value, track.source_key, track.title, track.artist, track.album,
            track.source_url, track.duration, track.cover_url,
        )

    async def add_favorite(self, user_id: int, track: QueueTrack) -> bool:
        return await asyncio.to_thread(self._add_favorite, user_id, track)

    def _add_favorite(self, user_id: int, track: QueueTrack) -> bool:
        with connect(self.database) as db:
            cursor = db.execute("""
                INSERT OR IGNORE INTO favorites(user_id,source_type,source_key,title,artist,album,source_url,duration,thumbnail)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (str(user_id), *self._snapshot(track)))
            return cursor.rowcount == 1

    async def remove_favorite(self, user_id: int, source_type: SourceType, source_key: str) -> bool:
        return await asyncio.to_thread(self._remove_favorite, user_id, source_type, source_key)

    def _remove_favorite(self, user_id: int, source_type: SourceType, source_key: str) -> bool:
        with connect(self.database) as db:
            return db.execute(
                "DELETE FROM favorites WHERE user_id=? AND source_type=? AND source_key=?",
                (str(user_id), source_type.value, source_key),
            ).rowcount == 1

    async def list_favorites(self, user_id: int, *, limit: int = 25, offset: int = 0):
        return await asyncio.to_thread(self._list_favorites, user_id, limit, offset)

    def _list_favorites(self, user_id: int, limit: int, offset: int):
        with connect(self.database) as db:
            return db.execute("""
                SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?
            """, (str(user_id), limit, offset)).fetchall()

    async def record_start(self, guild_id: int, track: QueueTrack) -> int:
        return await asyncio.to_thread(self._record_start, guild_id, track)

    def _record_start(self, guild_id: int, track: QueueTrack) -> int:
        with connect(self.database) as db:
            cursor = db.execute("""
                INSERT INTO play_history(guild_id,user_id,source_type,source_key,title,artist,album,source_url,duration)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (str(guild_id), str(track.requester_id), *self._snapshot(track)[:-1]))
            return int(cursor.lastrowid)

    async def complete(self, history_id: int) -> None:
        await asyncio.to_thread(self._complete, history_id)

    def _complete(self, history_id: int) -> None:
        with connect(self.database) as db:
            db.execute("UPDATE play_history SET completed=1 WHERE id=?", (history_id,))

    @staticmethod
    def _scope_values(scope: str, user_id: int, guild_id: int) -> tuple[str, str | None, str | None]:
        scope = scope.casefold().strip()
        if scope == "personal":
            return scope, str(user_id), None
        if scope == "server":
            return scope, None, str(guild_id)
        raise ValueError("Scope must be `personal` or `server`.")

    @staticmethod
    def _playlist_name(name: str) -> str:
        cleaned = " ".join(name.split())
        if not 1 <= len(cleaned) <= 100:
            raise ValueError("Playlist names must be between 1 and 100 characters.")
        return cleaned

    async def create_playlist(self, scope: str, name: str, user_id: int, guild_id: int) -> bool:
        return await asyncio.to_thread(self._create_playlist, scope, name, user_id, guild_id)

    def _create_playlist(self, scope: str, name: str, user_id: int, guild_id: int) -> bool:
        scope, owner, guild = self._scope_values(scope, user_id, guild_id)
        name = self._playlist_name(name)
        with connect(self.database) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO playlists(scope,owner_user_id,guild_id,name) VALUES(?,?,?,?)",
                (scope, owner, guild, name.strip()),
            )
            return cursor.rowcount == 1

    def _playlist_id(self, db, scope: str, name: str, user_id: int, guild_id: int) -> int | None:
        scope, owner, guild = self._scope_values(scope, user_id, guild_id)
        name = self._playlist_name(name)
        if scope == "personal":
            row = db.execute(
                "SELECT id FROM playlists WHERE scope='personal' AND owner_user_id=? AND name=? COLLATE NOCASE",
                (owner, name),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT id FROM playlists WHERE scope='server' AND guild_id=? AND name=? COLLATE NOCASE",
                (guild, name),
            ).fetchone()
        return int(row[0]) if row else None

    async def list_playlists(self, user_id: int, guild_id: int):
        return await asyncio.to_thread(self._list_playlists, user_id, guild_id)

    def _list_playlists(self, user_id: int, guild_id: int):
        with connect(self.database) as db:
            return db.execute("""
                SELECT p.id,p.scope,p.name,COUNT(i.id) AS track_count
                FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id=p.id
                WHERE (p.scope='personal' AND p.owner_user_id=?)
                   OR (p.scope='server' AND p.guild_id=?)
                GROUP BY p.id,p.scope,p.name
                ORDER BY p.scope,p.name COLLATE NOCASE
            """, (str(user_id), str(guild_id))).fetchall()

    async def add_playlist_item(
        self, scope: str, name: str, user_id: int, guild_id: int, track: QueueTrack
    ) -> bool:
        return await asyncio.to_thread(self._add_playlist_item, scope, name, user_id, guild_id, track)

    def _add_playlist_item(self, scope: str, name: str, user_id: int, guild_id: int, track: QueueTrack) -> bool:
        with connect(self.database) as db:
            playlist_id = self._playlist_id(db, scope, name, user_id, guild_id)
            if playlist_id is None:
                return False
            position = db.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM playlist_items WHERE playlist_id=?", (playlist_id,)
            ).fetchone()[0]
            db.execute("""
                INSERT INTO playlist_items(
                    playlist_id,source_type,source_key,title,artist,album,source_url,duration,thumbnail,position,added_by
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (playlist_id, *self._snapshot(track), position, str(user_id)))
            return True

    async def remove_playlist_item(
        self, scope: str, name: str, position: int, user_id: int, guild_id: int
    ) -> bool:
        return await asyncio.to_thread(self._remove_playlist_item, scope, name, position, user_id, guild_id)

    def _remove_playlist_item(self, scope: str, name: str, position: int, user_id: int, guild_id: int) -> bool:
        with connect(self.database) as db:
            playlist_id = self._playlist_id(db, scope, name, user_id, guild_id)
            if playlist_id is None:
                return False
            cursor = db.execute(
                "DELETE FROM playlist_items WHERE playlist_id=? AND position=?", (playlist_id, position)
            )
            if cursor.rowcount != 1:
                return False
            db.execute("""
                WITH ordered AS (
                    SELECT id,ROW_NUMBER() OVER (ORDER BY position,id) AS new_position
                    FROM playlist_items WHERE playlist_id=?
                )
                UPDATE playlist_items SET position=(SELECT new_position FROM ordered WHERE ordered.id=playlist_items.id)
                WHERE playlist_id=?
            """, (playlist_id, playlist_id))
            return True

    async def playlist_items(self, scope: str, name: str, user_id: int, guild_id: int):
        return await asyncio.to_thread(self._playlist_items, scope, name, user_id, guild_id)

    def _playlist_items(self, scope: str, name: str, user_id: int, guild_id: int):
        with connect(self.database) as db:
            playlist_id = self._playlist_id(db, scope, name, user_id, guild_id)
            if playlist_id is None:
                return []
            return db.execute(
                "SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position,id", (playlist_id,)
            ).fetchall()

    async def history(self, guild_id: int, user_id: int | None = None, limit: int = 15):
        return await asyncio.to_thread(self._history, guild_id, user_id, limit)

    def _history(self, guild_id: int, user_id: int | None, limit: int):
        with connect(self.database) as db:
            if user_id is None:
                return db.execute(
                    "SELECT * FROM play_history WHERE guild_id=? ORDER BY played_at DESC LIMIT ?",
                    (str(guild_id), limit),
                ).fetchall()
            return db.execute("""
                SELECT * FROM play_history WHERE guild_id=? AND user_id=? ORDER BY played_at DESC LIMIT ?
            """, (str(guild_id), str(user_id), limit)).fetchall()

    async def top(self, guild_id: int, field: str, limit: int = 10):
        if field not in {"title", "artist"}:
            raise ValueError("Statistics field is not supported")
        return await asyncio.to_thread(self._top, guild_id, field, limit)

    def _top(self, guild_id: int, field: str, limit: int):
        with connect(self.database) as db:
            return db.execute(f"""
                SELECT {field},COUNT(*) AS plays FROM play_history
                WHERE guild_id=? AND completed=1 GROUP BY {field}
                ORDER BY plays DESC,{field} COLLATE NOCASE LIMIT ?
            """, (str(guild_id), limit)).fetchall()

    async def stats(self, guild_id: int, user_id: int) -> dict[str, int]:
        return await asyncio.to_thread(self._stats, guild_id, user_id)

    def _stats(self, guild_id: int, user_id: int) -> dict[str, int]:
        with connect(self.database) as db:
            row = db.execute("""
                SELECT COUNT(*) AS plays,COALESCE(SUM(duration),0) AS seconds
                FROM play_history WHERE guild_id=? AND user_id=? AND completed=1
            """, (str(guild_id), str(user_id))).fetchone()
            return {"plays": int(row["plays"]), "seconds": int(row["seconds"])}
