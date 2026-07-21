const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');

const MAX_PAGE_SIZE = 25;
const SUPPORTED_SOURCES = new Set(['phyu', 'youtube', 'spotify', 'soundcloud', 'direct']);

function positiveInteger(value, fallback) {
    const parsed = Number.parseInt(value, 10);
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeOffset(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
}

function requiredIdentifier(value, fieldName) {
    const normalized = String(value || '').trim();
    if (!normalized || normalized.length > 64) {
        throw new TypeError(`${fieldName} must contain between 1 and 64 characters.`);
    }
    return normalized;
}

function requiredLocalDate(value) {
    const normalized = String(value || '');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
        throw new TypeError('localDate must use YYYY-MM-DD format.');
    }
    return normalized;
}

class DaisyStateStore {
    constructor(databasePath = process.env.STATE_DB_PATH || 'data/daisy_state.db') {
        this.databasePath = path.resolve(databasePath);
        fs.mkdirSync(path.dirname(this.databasePath), { recursive: true });

        this.database = new DatabaseSync(this.databasePath);
        this.database.exec('PRAGMA foreign_keys = ON');
        this.database.exec('PRAGMA busy_timeout = 5000');
        this.database.exec('PRAGMA journal_mode = WAL');
        this.migrate();
        this.prepareStatements();
    }

    migrate() {
        this.database.exec(`
            CREATE TABLE IF NOT EXISTS state_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            ) STRICT;
        `);

        const applied = new Set(
            this.database.prepare('SELECT version FROM state_schema_migrations').all()
                .map(row => row.version)
        );

        if (!applied.has(1)) {
            this.database.exec('BEGIN IMMEDIATE');
            try {
                this.database.exec(`
                    CREATE TABLE favorites (
                        id INTEGER PRIMARY KEY,
                        user_id TEXT NOT NULL CHECK(length(user_id) BETWEEN 1 AND 64),
                        source_type TEXT NOT NULL CHECK(source_type IN ('phyu', 'youtube', 'spotify', 'soundcloud', 'direct')),
                        source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 2048),
                        title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 1024),
                        artist TEXT,
                        album TEXT,
                        source_url TEXT,
                        duration INTEGER NOT NULL DEFAULT 0 CHECK(duration >= 0),
                        thumbnail TEXT,
                        playback_json TEXT NOT NULL CHECK(json_valid(playback_json)),
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(user_id, source_type, source_key)
                    ) STRICT;

                    CREATE INDEX idx_favorites_user_created
                    ON favorites(user_id, created_at DESC, id DESC);

                    INSERT INTO state_schema_migrations(version) VALUES (1);
                `);
                this.database.exec('COMMIT');
            } catch (error) {
                this.database.exec('ROLLBACK');
                throw error;
            }
        }

        if (!applied.has(2)) {
            this.database.exec('BEGIN IMMEDIATE');
            try {
                this.database.exec(`
                    CREATE TABLE daily_greetings (
                        guild_id TEXT NOT NULL CHECK(length(guild_id) BETWEEN 1 AND 64),
                        user_id TEXT NOT NULL CHECK(length(user_id) BETWEEN 1 AND 64),
                        local_date TEXT NOT NULL CHECK(
                            length(local_date) = 10
                            AND local_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                        ),
                        greeted_at TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (guild_id, user_id)
                    ) STRICT;

                    INSERT INTO state_schema_migrations(version) VALUES (2);
                `);
                this.database.exec('COMMIT');
            } catch (error) {
                this.database.exec('ROLLBACK');
                throw error;
            }
        }

        if (!applied.has(3)) {
            this.database.exec('BEGIN IMMEDIATE');
            try {
                this.database.exec(`
                    CREATE TABLE playlists (
                        id INTEGER PRIMARY KEY,
                        scope_type TEXT NOT NULL CHECK(scope_type IN ('personal', 'server')),
                        scope_id TEXT NOT NULL CHECK(length(scope_id) BETWEEN 1 AND 64),
                        created_by TEXT NOT NULL CHECK(length(created_by) BETWEEN 1 AND 64),
                        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 50),
                        name_key TEXT NOT NULL CHECK(length(name_key) BETWEEN 1 AND 50),
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(scope_type, scope_id, name_key)
                    ) STRICT;

                    CREATE INDEX idx_playlists_scope_name
                    ON playlists(scope_type, scope_id, name_key);

                    CREATE TABLE playlist_tracks (
                        id INTEGER PRIMARY KEY,
                        playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                        source_type TEXT NOT NULL CHECK(source_type IN ('phyu', 'youtube', 'spotify', 'soundcloud', 'direct')),
                        source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 2048),
                        title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 1024),
                        artist TEXT,
                        album TEXT,
                        source_url TEXT,
                        duration INTEGER NOT NULL DEFAULT 0 CHECK(duration >= 0),
                        thumbnail TEXT,
                        playback_json TEXT NOT NULL CHECK(json_valid(playback_json)),
                        position INTEGER NOT NULL CHECK(position > 0),
                        added_by TEXT NOT NULL CHECK(length(added_by) BETWEEN 1 AND 64),
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(playlist_id, source_type, source_key)
                    ) STRICT;

                    CREATE INDEX idx_playlist_tracks_position
                    ON playlist_tracks(playlist_id, position, id);

                    INSERT INTO state_schema_migrations(version) VALUES (3);
                `);
                this.database.exec('COMMIT');
            } catch (error) {
                this.database.exec('ROLLBACK');
                throw error;
            }
        }
    }

    prepareStatements() {
        this.statements = {
            favoriteByIdentity: this.database.prepare(`
                SELECT * FROM favorites
                WHERE user_id = ? AND source_type = ? AND source_key = ?
            `),
            upsertFavorite: this.database.prepare(`
                INSERT INTO favorites (
                    user_id, source_type, source_key, title, artist, album,
                    source_url, duration, thumbnail, playback_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, source_type, source_key) DO UPDATE SET
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    source_url = excluded.source_url,
                    duration = excluded.duration,
                    thumbnail = excluded.thumbnail,
                    playback_json = excluded.playback_json,
                    updated_at = datetime('now')
            `),
            removeFavorite: this.database.prepare(`
                DELETE FROM favorites
                WHERE user_id = ? AND source_type = ? AND source_key = ?
            `),
            removeFavoriteById: this.database.prepare(`
                DELETE FROM favorites WHERE id = ? AND user_id = ?
            `),
            favoriteById: this.database.prepare(`
                SELECT * FROM favorites WHERE id = ? AND user_id = ?
            `),
            listFavorites: this.database.prepare(`
                SELECT * FROM favorites
                WHERE user_id = $userId
                ORDER BY created_at DESC, id DESC
                LIMIT $limit OFFSET $offset
            `),
            countFavorites: this.database.prepare(`
                SELECT COUNT(*) AS count FROM favorites WHERE user_id = ?
            `),
            searchFavorites: this.database.prepare(`
                SELECT * FROM favorites
                WHERE user_id = $userId
                  AND (
                    title LIKE $pattern
                    OR COALESCE(artist, '') LIKE $pattern
                    OR COALESCE(album, '') LIKE $pattern
                  )
                ORDER BY
                    CASE WHEN title = $term THEN 0 WHEN title LIKE $prefix THEN 1 ELSE 2 END,
                    created_at DESC,
                    id DESC
                LIMIT $limit
            `),
            createPlaylist: this.database.prepare(`
                INSERT INTO playlists(scope_type, scope_id, created_by, name, name_key)
                VALUES (?, ?, ?, ?, ?)
            `),
            playlistById: this.database.prepare(`
                SELECT p.*,
                    (SELECT COUNT(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id) AS track_count
                FROM playlists p WHERE p.id = ?
            `),
            listPlaylists: this.database.prepare(`
                SELECT p.*,
                    (SELECT COUNT(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id) AS track_count
                FROM playlists p
                WHERE p.scope_type = ? AND p.scope_id = ?
                ORDER BY p.name_key, p.id
                LIMIT ?
            `),
            countPlaylists: this.database.prepare(`
                SELECT COUNT(*) AS count FROM playlists WHERE scope_type = ? AND scope_id = ?
            `),
            renamePlaylist: this.database.prepare(`
                UPDATE playlists
                SET name = ?, name_key = ?, updated_at = datetime('now')
                WHERE id = ?
            `),
            deletePlaylist: this.database.prepare('DELETE FROM playlists WHERE id = ?'),
            playlistTrackByIdentity: this.database.prepare(`
                SELECT * FROM playlist_tracks
                WHERE playlist_id = ? AND source_type = ? AND source_key = ?
            `),
            playlistTrackById: this.database.prepare(`
                SELECT * FROM playlist_tracks WHERE id = ? AND playlist_id = ?
            `),
            addPlaylistTrack: this.database.prepare(`
                INSERT INTO playlist_tracks (
                    playlist_id, source_type, source_key, title, artist, album,
                    source_url, duration, thumbnail, playback_json, position, added_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    (SELECT COALESCE(MAX(position), 0) + 1 FROM playlist_tracks WHERE playlist_id = ?), ?)
            `),
            listPlaylistTracks: this.database.prepare(`
                SELECT * FROM playlist_tracks
                WHERE playlist_id = $playlistId
                ORDER BY position, id
                LIMIT $limit OFFSET $offset
            `),
            allPlaylistTracks: this.database.prepare(`
                SELECT * FROM playlist_tracks WHERE playlist_id = ? ORDER BY position, id
            `),
            countPlaylistTracks: this.database.prepare(`
                SELECT COUNT(*) AS count FROM playlist_tracks WHERE playlist_id = ?
            `),
            deletePlaylistTrack: this.database.prepare(`
                DELETE FROM playlist_tracks WHERE id = ? AND playlist_id = ?
            `),
            updatePlaylistTrackPosition: this.database.prepare(`
                UPDATE playlist_tracks SET position = ?, updated_at = datetime('now') WHERE id = ?
            `),
            touchPlaylist: this.database.prepare(`
                UPDATE playlists SET updated_at = datetime('now') WHERE id = ?
            `),
            claimDailyGreeting: this.database.prepare(`
                INSERT INTO daily_greetings (guild_id, user_id, local_date)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    local_date = excluded.local_date,
                    greeted_at = datetime('now')
                WHERE daily_greetings.local_date <> excluded.local_date
            `),
            releaseDailyGreeting: this.database.prepare(`
                DELETE FROM daily_greetings
                WHERE guild_id = ? AND user_id = ? AND local_date = ?
            `),
        };
    }

    claimDailyGreeting(guildId, userId, localDate) {
        return this.statements.claimDailyGreeting.run(
            requiredIdentifier(guildId, 'guildId'),
            requiredIdentifier(userId, 'userId'),
            requiredLocalDate(localDate)
        ).changes > 0;
    }

    releaseDailyGreeting(guildId, userId, localDate) {
        return this.statements.releaseDailyGreeting.run(
            requiredIdentifier(guildId, 'guildId'),
            requiredIdentifier(userId, 'userId'),
            requiredLocalDate(localDate)
        ).changes > 0;
    }

    upsertFavorite(favorite) {
        this.validateFavorite(favorite);
        const existed = Boolean(this.statements.favoriteByIdentity.get(
            favorite.userId,
            favorite.sourceType,
            favorite.sourceKey
        ));

        this.statements.upsertFavorite.run(
            favorite.userId,
            favorite.sourceType,
            favorite.sourceKey,
            favorite.title,
            favorite.artist || null,
            favorite.album || null,
            favorite.sourceUrl || null,
            Math.max(0, Math.floor(Number(favorite.duration) || 0)),
            favorite.thumbnail || null,
            favorite.playbackJson
        );

        return {
            created: !existed,
            favorite: this.mapFavorite(this.statements.favoriteByIdentity.get(
                favorite.userId,
                favorite.sourceType,
                favorite.sourceKey
            )),
        };
    }

    removeFavorite(userId, sourceType, sourceKey) {
        return this.statements.removeFavorite.run(String(userId), sourceType, sourceKey).changes > 0;
    }

    hasFavorite(userId, sourceType, sourceKey) {
        return Boolean(this.statements.favoriteByIdentity.get(
            String(userId),
            sourceType,
            sourceKey
        ));
    }

    removeFavoriteById(userId, favoriteId) {
        const id = positiveInteger(favoriteId, 0);
        if (!id) return false;
        return this.statements.removeFavoriteById.run(id, String(userId)).changes > 0;
    }

    getFavoriteById(userId, favoriteId) {
        const id = positiveInteger(favoriteId, 0);
        if (!id) return null;
        const row = this.statements.favoriteById.get(id, String(userId));
        return row ? this.mapFavorite(row) : null;
    }

    listFavorites(userId, options = {}) {
        const rows = this.statements.listFavorites.all({
            $userId: String(userId),
            $limit: Math.min(positiveInteger(options.limit, MAX_PAGE_SIZE), MAX_PAGE_SIZE),
            $offset: normalizeOffset(options.offset),
        });
        return rows.map(row => this.mapFavorite(row));
    }

    countFavorites(userId) {
        return this.statements.countFavorites.get(String(userId)).count;
    }

    searchFavorites(userId, query, options = {}) {
        const term = String(query || '').trim();
        if (!term) return [];
        const rows = this.statements.searchFavorites.all({
            $userId: String(userId),
            $term: term,
            $prefix: `${term}%`,
            $pattern: `%${term}%`,
            $limit: Math.min(positiveInteger(options.limit, MAX_PAGE_SIZE), MAX_PAGE_SIZE),
        });
        return rows.map(row => this.mapFavorite(row));
    }

    createPlaylist(playlist) {
        const result = this.statements.createPlaylist.run(
            playlist.scopeType,
            playlist.scopeId,
            playlist.createdBy,
            playlist.name,
            playlist.nameKey
        );
        return this.getPlaylistById(result.lastInsertRowid);
    }

    getPlaylistById(playlistId) {
        const id = positiveInteger(playlistId, 0);
        if (!id) return null;
        const row = this.statements.playlistById.get(id);
        return row ? this.mapPlaylist(row) : null;
    }

    listPlaylists(scopeType, scopeId, options = {}) {
        const rows = this.statements.listPlaylists.all(
            scopeType,
            String(scopeId),
            Math.min(positiveInteger(options.limit, MAX_PAGE_SIZE), MAX_PAGE_SIZE)
        );
        return rows.map(row => this.mapPlaylist(row));
    }

    countPlaylists(scopeType, scopeId) {
        return this.statements.countPlaylists.get(scopeType, String(scopeId)).count;
    }

    renamePlaylist(playlistId, name, nameKey) {
        const id = positiveInteger(playlistId, 0);
        if (!id) return null;
        if (!this.statements.renamePlaylist.run(name, nameKey, id).changes) return null;
        return this.getPlaylistById(id);
    }

    deletePlaylist(playlistId) {
        const id = positiveInteger(playlistId, 0);
        return id ? this.statements.deletePlaylist.run(id).changes > 0 : false;
    }

    addPlaylistTrack(track) {
        const result = this.statements.addPlaylistTrack.run(
            track.playlistId,
            track.sourceType,
            track.sourceKey,
            track.title,
            track.artist || null,
            track.album || null,
            track.sourceUrl || null,
            Math.max(0, Math.floor(Number(track.duration) || 0)),
            track.thumbnail || null,
            track.playbackJson,
            track.playlistId,
            track.addedBy
        );
        this.statements.touchPlaylist.run(track.playlistId);
        return this.mapPlaylistTrack(this.statements.playlistTrackById.get(
            result.lastInsertRowid,
            track.playlistId
        ));
    }

    hasPlaylistTrack(playlistId, sourceType, sourceKey) {
        return Boolean(this.statements.playlistTrackByIdentity.get(
            positiveInteger(playlistId, 0),
            sourceType,
            sourceKey
        ));
    }

    getPlaylistTrackById(playlistId, trackId) {
        const row = this.statements.playlistTrackById.get(
            positiveInteger(trackId, 0),
            positiveInteger(playlistId, 0)
        );
        return row ? this.mapPlaylistTrack(row) : null;
    }

    listPlaylistTracks(playlistId, options = {}) {
        const rows = this.statements.listPlaylistTracks.all({
            $playlistId: positiveInteger(playlistId, 0),
            $limit: Math.min(positiveInteger(options.limit, MAX_PAGE_SIZE), MAX_PAGE_SIZE),
            $offset: normalizeOffset(options.offset),
        });
        return rows.map(row => this.mapPlaylistTrack(row));
    }

    listAllPlaylistTracks(playlistId) {
        return this.statements.allPlaylistTracks.all(positiveInteger(playlistId, 0))
            .map(row => this.mapPlaylistTrack(row));
    }

    countPlaylistTracks(playlistId) {
        return this.statements.countPlaylistTracks.get(positiveInteger(playlistId, 0)).count;
    }

    removePlaylistTrack(playlistId, trackId) {
        const playlist = positiveInteger(playlistId, 0);
        const track = positiveInteger(trackId, 0);
        if (!playlist || !track) return false;
        this.database.exec('BEGIN IMMEDIATE');
        try {
            const removed = this.statements.deletePlaylistTrack.run(track, playlist).changes > 0;
            if (removed) this.resequencePlaylistTracks(playlist);
            this.database.exec('COMMIT');
            return removed;
        } catch (error) {
            this.database.exec('ROLLBACK');
            throw error;
        }
    }

    movePlaylistTrack(playlistId, trackId, direction) {
        const playlist = positiveInteger(playlistId, 0);
        const track = positiveInteger(trackId, 0);
        const tracks = this.statements.allPlaylistTracks.all(playlist);
        const index = tracks.findIndex(row => row.id === track);
        const target = direction === 'up' ? index - 1 : direction === 'down' ? index + 1 : index;
        if (index < 0 || target < 0 || target >= tracks.length || target === index) return false;
        [tracks[index], tracks[target]] = [tracks[target], tracks[index]];
        this.database.exec('BEGIN IMMEDIATE');
        try {
            tracks.forEach((row, position) => {
                this.statements.updatePlaylistTrackPosition.run(position + 1, row.id);
            });
            this.statements.touchPlaylist.run(playlist);
            this.database.exec('COMMIT');
            return true;
        } catch (error) {
            this.database.exec('ROLLBACK');
            throw error;
        }
    }

    resequencePlaylistTracks(playlistId) {
        const tracks = this.statements.allPlaylistTracks.all(playlistId);
        tracks.forEach((row, position) => {
            this.statements.updatePlaylistTrackPosition.run(position + 1, row.id);
        });
        this.statements.touchPlaylist.run(playlistId);
    }

    validateFavorite(favorite) {
        if (!favorite || typeof favorite !== 'object') throw new TypeError('Favorite data is required.');
        if (!favorite.userId) throw new TypeError('Favorite userId is required.');
        if (!SUPPORTED_SOURCES.has(favorite.sourceType)) {
            throw new TypeError(`Unsupported favorite source: ${favorite.sourceType || 'missing'}.`);
        }
        if (!favorite.sourceKey || !favorite.title) {
            throw new TypeError('Favorite sourceKey and title are required.');
        }
        try {
            JSON.parse(favorite.playbackJson);
        } catch {
            throw new TypeError('Favorite playbackJson must be valid JSON.');
        }
    }

    mapFavorite(row) {
        return {
            id: row.id,
            userId: row.user_id,
            sourceType: row.source_type,
            sourceKey: row.source_key,
            title: row.title,
            artist: row.artist,
            album: row.album,
            sourceUrl: row.source_url,
            duration: row.duration,
            thumbnail: row.thumbnail,
            playbackJson: row.playback_json,
            createdAt: row.created_at,
            updatedAt: row.updated_at,
        };
    }

    mapPlaylist(row) {
        return {
            id: row.id,
            scopeType: row.scope_type,
            scopeId: row.scope_id,
            createdBy: row.created_by,
            name: row.name,
            nameKey: row.name_key,
            trackCount: Number(row.track_count || 0),
            createdAt: row.created_at,
            updatedAt: row.updated_at,
        };
    }

    mapPlaylistTrack(row) {
        return {
            id: row.id,
            playlistId: row.playlist_id,
            sourceType: row.source_type,
            sourceKey: row.source_key,
            title: row.title,
            artist: row.artist,
            album: row.album,
            sourceUrl: row.source_url,
            duration: row.duration,
            thumbnail: row.thumbnail,
            playbackJson: row.playback_json,
            position: row.position,
            addedBy: row.added_by,
            createdAt: row.created_at,
            updatedAt: row.updated_at,
        };
    }

    close() {
        if (this.database?.isOpen) this.database.close();
    }
}

module.exports = DaisyStateStore;
