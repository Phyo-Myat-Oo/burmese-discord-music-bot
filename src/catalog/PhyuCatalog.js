const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');

const EXPECTED_SCHEMA_VERSION = '2';
const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 25;

const TRACK_SELECT = `
    SELECT
        t.id,
        t.title AS source_title,
        t.display_title AS title,
        t.search_key,
        t.size,
        t.content_type,
        t.duration,
        t.artist_text,
        CAST(t.pcloud_file_id AS TEXT) AS pcloud_file_id,
        t.mediafire_quick_key,
        a.id AS album_id,
        a.display_title AS album,
        a.pcloud_code,
        a.share_url AS pcloud_share_url,
        a.mediafire_url,
        a.post_url,
        p.title AS post_title,
        p.cover_url,
        (
            SELECT group_concat(artist_name, ' / ')
            FROM (
                SELECT ar.name AS artist_name
                FROM track_artists ta
                JOIN artists ar ON ar.id = ta.artist_id
                WHERE ta.track_id = t.id
                ORDER BY ta.position, ar.id
            )
        ) AS artist_names
    FROM tracks t
    JOIN albums a ON a.id = t.album_id
    JOIN posts p ON p.url = a.post_url
`;

const ALBUM_SELECT = `
    SELECT
        a.id,
        a.title AS source_title,
        a.display_title AS title,
        a.search_key,
        a.pcloud_code,
        a.share_url AS pcloud_share_url,
        a.mediafire_url,
        a.post_url,
        a.scanned_at,
        a.error,
        p.title AS post_title,
        p.published,
        p.source_updated,
        p.cover_url,
        (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count,
        (
            SELECT group_concat(artist_name, ' / ')
            FROM (
                SELECT ar.name AS artist_name
                FROM album_artists aa
                JOIN artists ar ON ar.id = aa.artist_id
                WHERE aa.album_id = a.id
                ORDER BY aa.position, ar.id
            )
        ) AS artist_names
    FROM albums a
    JOIN posts p ON p.url = a.post_url
`;

const SEARCH_ITEMS_SELECT = `
    SELECT item_type, id, title, artist, album, cover_url, track_count
    FROM (
        SELECT
            'track' AS item_type,
            t.id,
            t.display_title AS title,
            COALESCE((
                SELECT group_concat(artist_name, ' / ')
                FROM (
                    SELECT ar.name AS artist_name
                    FROM track_artists ta
                    JOIN artists ar ON ar.id = ta.artist_id
                    WHERE ta.track_id = t.id
                    ORDER BY ta.position, ar.id
                )
            ), t.artist_text, '') AS artist,
            a.display_title AS album,
            p.cover_url,
            1 AS track_count,
            CASE
                WHEN t.search_key = $term THEN 0
                WHEN t.search_key LIKE $prefix THEN 2
                WHEN a.search_key = $term THEN 4
                WHEN a.search_key LIKE $prefix THEN 5
                ELSE 8
            END AS match_rank
        FROM tracks t
        JOIN albums a ON a.id = t.album_id
        JOIN posts p ON p.url = a.post_url
        WHERE
            t.search_key LIKE $pattern
            OR a.search_key LIKE $pattern
            OR EXISTS (
                SELECT 1
                FROM track_artists ta
                JOIN artists ar ON ar.id = ta.artist_id
                WHERE ta.track_id = t.id AND ar.search_key LIKE $pattern
            )

        UNION ALL

        SELECT
            'album' AS item_type,
            a.id,
            a.display_title AS title,
            COALESCE((
                SELECT group_concat(artist_name, ' / ')
                FROM (
                    SELECT ar.name AS artist_name
                    FROM album_artists aa
                    JOIN artists ar ON ar.id = aa.artist_id
                    WHERE aa.album_id = a.id
                    ORDER BY aa.position, ar.id
                )
            ), '') AS artist,
            NULL AS album,
            p.cover_url,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count,
            CASE
                WHEN a.search_key = $term THEN 1
                WHEN a.search_key LIKE $prefix THEN 3
                ELSE 7
            END AS match_rank
        FROM albums a
        JOIN posts p ON p.url = a.post_url
        WHERE
            a.search_key LIKE $pattern
            OR EXISTS (
                SELECT 1
                FROM album_artists aa
                JOIN artists ar ON ar.id = aa.artist_id
                WHERE aa.album_id = a.id AND ar.search_key LIKE $pattern
            )
    )
    ORDER BY match_rank, title, item_type, id
    LIMIT $limit OFFSET $offset
`;

function clampLimit(value) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed) || parsed < 1) return DEFAULT_LIMIT;
    return Math.min(parsed, MAX_LIMIT);
}

function normalizeOffset(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function normalizeId(value, label) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isSafeInteger(parsed) || parsed < 1) {
        throw new TypeError(`${label} must be a positive integer.`);
    }
    return parsed;
}

class PhyuCatalog {
    constructor(databasePath = process.env.CATALOG_DB_PATH || 'data/daisy_v2/catalog.db') {
        this.databasePath = path.resolve(databasePath);

        if (!fs.existsSync(this.databasePath)) {
            throw new Error(`Phyu catalogue database not found: ${this.databasePath}`);
        }

        this.database = new DatabaseSync(this.databasePath, { readOnly: true });
        this.validateSchema();
        this.prepareStatements();
    }

    static normalizeSearchText(value) {
        if (value === null || value === undefined) return '';

        return String(value)
            .normalize('NFKC')
            .toLowerCase()
            .replace(/[၀-၉]/g, digit => String(digit.charCodeAt(0) - 0x1040))
            .replace(/[^\p{L}\p{M}\p{N}]+/gu, '');
    }

    validateSchema() {
        const integrity = this.database.prepare('PRAGMA quick_check').get();
        if (integrity.quick_check !== 'ok') {
            throw new Error(`Phyu catalogue integrity check failed: ${integrity.quick_check}`);
        }

        const version = this.database
            .prepare("SELECT value FROM schema_meta WHERE key = 'schema_version'")
            .get();

        if (!version || version.value !== EXPECTED_SCHEMA_VERSION) {
            throw new Error(
                `Unsupported Phyu catalogue schema version: ${version?.value || 'missing'} ` +
                `(expected ${EXPECTED_SCHEMA_VERSION}).`
            );
        }

        const requiredTables = [
            'posts', 'albums', 'tracks', 'artists', 'album_artists', 'track_artists'
        ];
        const tableRows = this.database
            .prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
            .all();
        const availableTables = new Set(tableRows.map(row => row.name));
        const missingTables = requiredTables.filter(table => !availableTables.has(table));

        if (missingTables.length > 0) {
            throw new Error(`Phyu catalogue is missing tables: ${missingTables.join(', ')}`);
        }
    }

    prepareStatements() {
        this.statements = {
            stats: this.database.prepare(`
                SELECT
                    (SELECT COUNT(*) FROM posts) AS posts,
                    (SELECT COUNT(*) FROM albums) AS albums,
                    (SELECT COUNT(*) FROM artists) AS artists,
                    (SELECT COUNT(*) FROM tracks) AS tracks,
                    (SELECT COUNT(*) FROM albums WHERE error IS NOT NULL AND trim(error) <> '') AS failed_albums,
                    (SELECT COUNT(*) FROM tracks WHERE mediafire_quick_key IS NOT NULL AND trim(mediafire_quick_key) <> '') AS mediafire_tracks
            `),
            trackById: this.database.prepare(`${TRACK_SELECT} WHERE t.id = ?`),
            albumById: this.database.prepare(`${ALBUM_SELECT} WHERE a.id = ?`),
            albumTracks: this.database.prepare(`${TRACK_SELECT} WHERE t.album_id = ? ORDER BY t.id`),
            albumTracksPage: this.database.prepare(`
                ${TRACK_SELECT}
                WHERE t.album_id = $albumId
                ORDER BY t.id
                LIMIT $limit OFFSET $offset
            `),
            artistById: this.database.prepare(`
                SELECT
                    ar.id,
                    ar.name,
                    ar.search_key,
                    (SELECT COUNT(*) FROM album_artists aa WHERE aa.artist_id = ar.id) AS album_count,
                    (SELECT COUNT(*) FROM track_artists ta WHERE ta.artist_id = ar.id) AS track_count
                FROM artists ar
                WHERE ar.id = ?
            `),
            albumsByArtist: this.database.prepare(`
                ${ALBUM_SELECT}
                WHERE EXISTS (
                    SELECT 1 FROM album_artists aa
                    WHERE aa.album_id = a.id AND aa.artist_id = $artistId
                )
                ORDER BY a.display_title, a.id
                LIMIT $limit OFFSET $offset
            `),
            listArtists: this.database.prepare(`
                SELECT
                    ar.id,
                    ar.name,
                    ar.search_key,
                    (SELECT COUNT(*) FROM album_artists aa WHERE aa.artist_id = ar.id) AS album_count,
                    (SELECT COUNT(*) FROM track_artists ta WHERE ta.artist_id = ar.id) AS track_count
                FROM artists ar
                ORDER BY ar.name, ar.id
                LIMIT $limit OFFSET $offset
            `),
            searchArtists: this.database.prepare(`
                SELECT
                    ar.id,
                    ar.name,
                    ar.search_key,
                    (SELECT COUNT(*) FROM album_artists aa WHERE aa.artist_id = ar.id) AS album_count,
                    (SELECT COUNT(*) FROM track_artists ta WHERE ta.artist_id = ar.id) AS track_count
                FROM artists ar
                WHERE ar.search_key LIKE $pattern
                ORDER BY
                    CASE
                        WHEN ar.search_key = $term THEN 0
                        WHEN ar.search_key LIKE $prefix THEN 1
                        ELSE 2
                    END,
                    ar.name,
                    ar.id
                LIMIT $limit OFFSET $offset
            `),
            searchAlbums: this.database.prepare(`
                ${ALBUM_SELECT}
                WHERE
                    a.search_key LIKE $pattern
                    OR EXISTS (
                        SELECT 1
                        FROM album_artists aa
                        JOIN artists ar ON ar.id = aa.artist_id
                        WHERE aa.album_id = a.id AND ar.search_key LIKE $pattern
                    )
                ORDER BY
                    CASE
                        WHEN a.search_key = $term THEN 0
                        WHEN a.search_key LIKE $prefix THEN 1
                        ELSE 2
                    END,
                    a.display_title,
                    a.id
                LIMIT $limit OFFSET $offset
            `),
            searchTracks: this.database.prepare(`
                ${TRACK_SELECT}
                WHERE
                    t.search_key LIKE $pattern
                    OR a.search_key LIKE $pattern
                    OR EXISTS (
                        SELECT 1
                        FROM track_artists ta
                        JOIN artists ar ON ar.id = ta.artist_id
                        WHERE ta.track_id = t.id AND ar.search_key LIKE $pattern
                    )
                ORDER BY
                    CASE
                        WHEN t.search_key = $term THEN 0
                        WHEN t.search_key LIKE $prefix THEN 1
                        WHEN a.search_key LIKE $prefix THEN 2
                        ELSE 3
                    END,
                    t.id
                LIMIT $limit OFFSET $offset
            `),
            searchItems: this.database.prepare(SEARCH_ITEMS_SELECT),
            randomTrack: this.database.prepare(`${TRACK_SELECT} ORDER BY random() LIMIT 1`),
            randomTrackByArtist: this.database.prepare(`
                ${TRACK_SELECT}
                WHERE EXISTS (
                    SELECT 1 FROM track_artists ta
                    WHERE ta.track_id = t.id AND ta.artist_id = ?
                )
                ORDER BY random()
                LIMIT 1
            `),
            randomAlbum: this.database.prepare(`${ALBUM_SELECT} ORDER BY random() LIMIT 1`),
        };
    }

    getStats() {
        return this.mapStats(this.statements.stats.get());
    }

    getTrackById(trackId) {
        const row = this.statements.trackById.get(normalizeId(trackId, 'trackId'));
        return row ? this.mapTrack(row) : null;
    }

    getAlbumById(albumId) {
        const row = this.statements.albumById.get(normalizeId(albumId, 'albumId'));
        return row ? this.mapAlbum(row) : null;
    }

    getAlbumTracks(albumId) {
        const rows = this.statements.albumTracks.all(normalizeId(albumId, 'albumId'));
        return rows.map(row => this.mapTrack(row));
    }

    getAlbumTracksPage(albumId, options = {}) {
        const rows = this.statements.albumTracksPage.all({
            $albumId: normalizeId(albumId, 'albumId'),
            $limit: clampLimit(options.limit),
            $offset: normalizeOffset(options.offset),
        });
        return rows.map(row => this.mapTrack(row));
    }

    getArtistById(artistId) {
        const row = this.statements.artistById.get(normalizeId(artistId, 'artistId'));
        return row ? this.mapArtist(row) : null;
    }

    getAlbumsByArtist(artistId, options = {}) {
        const rows = this.statements.albumsByArtist.all({
            $artistId: normalizeId(artistId, 'artistId'),
            $limit: clampLimit(options.limit),
            $offset: normalizeOffset(options.offset),
        });
        return rows.map(row => this.mapAlbum(row));
    }

    listArtists(options = {}) {
        const rows = this.statements.listArtists.all({
            $limit: clampLimit(options.limit),
            $offset: normalizeOffset(options.offset),
        });
        return rows.map(row => this.mapArtist(row));
    }

    searchArtists(query, options = {}) {
        const term = PhyuCatalog.normalizeSearchText(query);
        if (!term) return this.listArtists(options);

        const rows = this.statements.searchArtists.all(this.searchBindings(term, options));
        return rows.map(row => this.mapArtist(row));
    }

    searchAlbums(query, options = {}) {
        const term = PhyuCatalog.normalizeSearchText(query);
        if (!term) return [];

        const rows = this.statements.searchAlbums.all(this.searchBindings(term, options));
        return rows.map(row => this.mapAlbum(row));
    }

    searchTracks(query, options = {}) {
        const term = PhyuCatalog.normalizeSearchText(query);
        if (!term) return [];

        const rows = this.statements.searchTracks.all(this.searchBindings(term, options));
        return rows.map(row => this.mapTrack(row));
    }

    searchItems(query, options = {}) {
        const term = PhyuCatalog.normalizeSearchText(query);
        if (!term) return [];

        const rows = this.statements.searchItems.all(this.searchBindings(term, options));
        return rows.map(row => ({
            type: row.item_type,
            id: row.id,
            stableKey: `phyu:${row.item_type}:${row.id}`,
            title: row.title,
            artist: row.artist || '',
            album: row.album || null,
            coverUrl: row.cover_url || null,
            trackCount: row.track_count,
        }));
    }

    getRandomTrack(options = {}) {
        const row = options.artistId
            ? this.statements.randomTrackByArtist.get(normalizeId(options.artistId, 'artistId'))
            : this.statements.randomTrack.get();
        return row ? this.mapTrack(row) : null;
    }

    getRandomAlbum() {
        const row = this.statements.randomAlbum.get();
        return row ? this.mapAlbum(row) : null;
    }

    searchBindings(term, options) {
        return {
            $term: term,
            $prefix: `${term}%`,
            $pattern: `%${term}%`,
            $limit: clampLimit(options.limit),
            $offset: normalizeOffset(options.offset),
        };
    }

    mapTrack(row) {
        return {
            id: row.id,
            stableKey: `phyu:track:${row.id}`,
            title: row.title,
            sourceTitle: row.source_title,
            artist: row.artist_names || row.artist_text || '',
            artistText: row.artist_text || '',
            albumId: row.album_id,
            album: row.album,
            duration: row.duration || null,
            size: row.size,
            contentType: row.content_type,
            coverUrl: row.cover_url,
            postTitle: row.post_title,
            postUrl: row.post_url,
            pcloudCode: row.pcloud_code,
            pcloudFileId: row.pcloud_file_id,
            pcloudShareUrl: row.pcloud_share_url,
            mediafireUrl: row.mediafire_url,
            mediafireQuickKey: row.mediafire_quick_key,
        };
    }

    mapAlbum(row) {
        return {
            id: row.id,
            stableKey: `phyu:album:${row.id}`,
            title: row.title,
            sourceTitle: row.source_title,
            artist: row.artist_names || '',
            trackCount: row.track_count,
            coverUrl: row.cover_url,
            postTitle: row.post_title,
            postUrl: row.post_url,
            published: row.published,
            sourceUpdated: row.source_updated,
            scannedAt: row.scanned_at,
            error: row.error,
            pcloudCode: row.pcloud_code,
            pcloudShareUrl: row.pcloud_share_url,
            mediafireUrl: row.mediafire_url,
        };
    }

    mapArtist(row) {
        return {
            id: row.id,
            stableKey: `phyu:artist:${row.id}`,
            name: row.name,
            albumCount: row.album_count,
            trackCount: row.track_count,
        };
    }

    mapStats(row) {
        return {
            posts: row.posts,
            albums: row.albums,
            artists: row.artists,
            tracks: row.tracks,
            failedAlbums: row.failed_albums,
            mediafireTracks: row.mediafire_tracks,
        };
    }

    close() {
        if (this.database?.isOpen) {
            this.database.close();
        }
    }
}

module.exports = PhyuCatalog;
