const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');
const PhyuCatalog = require('./PhyuCatalog');

const DEFAULT_SITE = 'https://phyuniwarpyar.blogspot.com';
const PCLOUD_HOSTS = ['api.pcloud.com', 'eapi.pcloud.com'];
const AUDIO_EXTENSION = /\.(?:mp3|m4a|aac|ogg|opus|wav|flac|wma)$/i;

function decodeHtml(value) {
    return String(value || '')
        .replace(/&amp;/gi, '&')
        .replace(/&quot;/gi, '"')
        .replace(/&#39;|&apos;/gi, "'")
        .replace(/&lt;/gi, '<')
        .replace(/&gt;/gi, '>')
        .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
        .replace(/&#(\d+);/g, (_, decimal) => String.fromCodePoint(Number.parseInt(decimal, 10)));
}

function decodeRepeated(value) {
    let result = decodeHtml(value);
    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            const decoded = decodeURIComponent(result);
            if (decoded === result) break;
            result = decoded;
        } catch (_) {
            break;
        }
    }
    return result;
}

function uniqueBy(items, keySelector) {
    const seen = new Set();
    return items.filter(item => {
        const key = keySelector(item);
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function cleanDisplayTitle(value) {
    return decodeRepeated(value)
        .normalize('NFKC')
        .replace(AUDIO_EXTENSION, '')
        .replace(/^\s*[0-9၀-၉]{1,3}\s*[.)_-]+\s*/u, '')
        .replace(/\s*\((?:128|192|256|320)[^)]*\)\s*$/iu, '')
        .replace(/[_+]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function matchingKey(value) {
    return PhyuCatalog.normalizeSearchText(cleanDisplayTitle(value));
}

function trackNumber(value) {
    const match = decodeRepeated(value).match(/^\s*([0-9၀-၉]{1,3})\s*[.)_-]/u);
    if (!match) return null;
    const normalized = match[1].replace(/[၀-၉]/g, digit => String(digit.charCodeAt(0) - 0x1040));
    return Number.parseInt(normalized, 10);
}

function extractArtistNames(value) {
    const display = cleanDisplayTitle(value);
    const prefix = display.includes(' - ') ? display.split(' - ')[0] : display;
    return uniqueBy(
        prefix.split(/(?:၊|,|\/|&|\+|\s+နှင့်\s+)/u).map(name => name.trim()).filter(Boolean),
        name => PhyuCatalog.normalizeSearchText(name)
    );
}

function trackArtist(value, fallback = '') {
    const display = cleanDisplayTitle(value);
    const parts = display.split(/\s+-\s+/u).map(part => part.trim()).filter(Boolean);
    return parts.length > 1 ? parts.at(-1) : fallback;
}

function extractSourceLinks(html, baseUrl = DEFAULT_SITE) {
    const hrefs = [];
    for (const match of String(html || '').matchAll(/href\s*=\s*["']([^"']+)["']/gi)) {
        try {
            hrefs.push(new URL(decodeHtml(match[1]), baseUrl));
        } catch (_) {
        }
    }

    const pcloud = [];
    const mediafireFolders = [];
    const mediafireFiles = [];
    for (const url of hrefs) {
        const hostname = url.hostname.toLowerCase();
        if (hostname.includes('pcloud.')) {
            const code = url.searchParams.get('code');
            if (code) pcloud.push({ code, url: url.toString() });
            continue;
        }
        if (!hostname.endsWith('mediafire.com')) continue;

        const folder = url.pathname.match(/\/folder\/([a-z0-9]+)/i);
        if (folder) {
            mediafireFolders.push({ key: folder[1], url: url.toString() });
            continue;
        }
        const file = url.pathname.match(/\/file(?:_premium)?\/([a-z0-9]{15})(?:\/([^/]+))?/i);
        if (file) {
            mediafireFiles.push({
                quickkey: file[1],
                filename: file[2] ? decodeRepeated(file[2]).replace(/\/file$/i, '') : file[1],
                url: url.toString(),
            });
        }
    }

    const imageMatch = String(html || '').match(/<img[^>]+src\s*=\s*["'](https?:\/\/[^"']+)["']/i);
    return {
        pcloud: uniqueBy(pcloud, item => item.code),
        mediafireFolders: uniqueBy(mediafireFolders, item => item.key),
        mediafireFiles: uniqueBy(mediafireFiles, item => item.quickkey),
        coverUrl: imageMatch ? decodeHtml(imageMatch[1]) : null,
    };
}

function parseFeedEntry(entry) {
    const url = entry?.link?.find(link => link.rel === 'alternate')?.href;
    if (!url) return null;
    const html = entry.content?.$t || entry.summary?.$t || '';
    const links = extractSourceLinks(html, url);
    return {
        url,
        title: decodeHtml(entry.title?.$t || 'Untitled post').trim(),
        published: entry.published?.$t || null,
        updated: entry.updated?.$t || '',
        html,
        coverUrl: entry.media$thumbnail?.url || links.coverUrl,
        links,
    };
}

function parsePCloudJson(text) {
    return JSON.parse(String(text).replace(/("fileid"\s*:\s*)(\d{16,})/g, '$1"$2"'));
}

function flattenPCloudFiles(metadata, files = []) {
    if (!metadata) return files;
    if (!metadata.isfolder) {
        const contentType = metadata.contenttype || '';
        if (String(contentType).startsWith('audio/') || AUDIO_EXTENSION.test(metadata.name || '')) {
            files.push(metadata);
        }
        return files;
    }
    for (const child of metadata.contents || []) flattenPCloudFiles(child, files);
    return files;
}

function matchMediaFireFiles(tracks, files) {
    const available = [...files];
    const matches = [];
    for (const track of tracks) {
        const key = matchingKey(track.title);
        let index = available.findIndex(file => matchingKey(file.filename) === key);
        if (index < 0) {
            const number = trackNumber(track.title);
            if (number !== null) {
                const numbered = available
                    .map((file, fileIndex) => ({ file, fileIndex }))
                    .filter(candidate => trackNumber(candidate.file.filename) === number);
                if (numbered.length === 1) index = numbered[0].fileIndex;
            }
        }
        if (index < 0) continue;
        matches.push({ track, file: available[index] });
        available.splice(index, 1);
    }
    return matches;
}

class CatalogueUpdater {
    constructor(options = {}) {
        this.databasePath = path.resolve(options.databasePath || process.env.CATALOG_DB_PATH || 'data/daisy_v2/catalog.db');
        this.siteUrl = options.siteUrl || DEFAULT_SITE;
        this.fetch = options.fetch || global.fetch;
        this.logger = options.logger || console;
        this.timeoutMs = options.timeoutMs || 30_000;
        this.dryRun = Boolean(options.dryRun);
        this.database = null;
        this.lockPath = `${this.databasePath}.update.lock`;
        this.lockFd = null;
        if (typeof this.fetch !== 'function') throw new TypeError('CatalogueUpdater requires fetch.');
    }

    async run(options = {}) {
        const recentPosts = Math.max(1, Number.parseInt(options.recentPosts || 100, 10));
        const backfillPosts = Math.max(0, Number.parseInt(options.backfillPosts || 50, 10));
        this.acquireLock();
        const stats = {
            feedEntries: 0,
            changedPosts: 0,
            skippedPosts: 0,
            albumsUpdated: 0,
            tracksUpdated: 0,
            mediafireTracksUpdated: 0,
            backfillPosts: 0,
            errors: 0,
        };

        try {
            this.openDatabase();
            if (options.backup && !this.dryRun) this.createBackup();
            const entries = await this.fetchRecentFeed(recentPosts);
            stats.feedEntries = entries.length;
            for (let index = 0; index < entries.length; index += 1) {
                const entry = entries[index];
                const existing = this.database.prepare('SELECT source_updated FROM posts WHERE url = ?').get(entry.url);
                if (existing?.source_updated === entry.updated) {
                    stats.skippedPosts += 1;
                    continue;
                }
                this.logger.log(`[catalogue] Recent ${index + 1}/${entries.length}: ${entry.title}`);
                try {
                    await this.updatePost(entry, stats);
                    stats.changedPosts += 1;
                } catch (error) {
                    stats.errors += 1;
                    this.logger.error(`[catalogue] Failed ${entry.url}: ${error.message}`);
                }
            }

            if (backfillPosts > 0) await this.backfillMediaFire(backfillPosts, stats);
            if (!this.dryRun) {
                this.database.exec('PRAGMA optimize');
                const check = this.database.prepare('PRAGMA quick_check').get();
                if (check.quick_check !== 'ok') throw new Error(`Catalogue quick_check failed: ${check.quick_check}`);
            }
            return stats;
        } finally {
            this.close();
            this.releaseLock();
        }
    }

    acquireLock() {
        fs.mkdirSync(path.dirname(this.databasePath), { recursive: true });
        try {
            this.lockFd = fs.openSync(this.lockPath, 'wx');
            fs.writeFileSync(this.lockFd, `${process.pid}\n${new Date().toISOString()}\n`);
        } catch (error) {
            if (error.code === 'EEXIST') throw new Error(`Catalogue update already running (${this.lockPath}).`);
            throw error;
        }
    }

    releaseLock() {
        if (this.lockFd !== null) fs.closeSync(this.lockFd);
        this.lockFd = null;
        fs.rmSync(this.lockPath, { force: true });
    }

    openDatabase() {
        if (!fs.existsSync(this.databasePath)) throw new Error(`Catalogue database not found: ${this.databasePath}`);
        this.database = new DatabaseSync(this.databasePath);
        this.database.exec('PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 15000;');
        const version = this.database.prepare("SELECT value FROM schema_meta WHERE key = 'schema_version'").get()?.value;
        if (version !== '2') throw new Error(`Unsupported catalogue schema version: ${version || 'missing'}`);
    }

    close() {
        if (this.database?.isOpen) this.database.close();
        this.database = null;
    }

    createBackup() {
        const backupDirectory = path.join(path.dirname(this.databasePath), 'backups');
        fs.mkdirSync(backupDirectory, { recursive: true });
        const stamp = new Date().toISOString().replace(/[:.]/g, '-');
        const backupPath = path.join(backupDirectory, `catalog-${stamp}.db`);
        this.database.exec(`VACUUM INTO '${backupPath.replaceAll("'", "''")}'`);
        const backups = fs.readdirSync(backupDirectory)
            .filter(file => /^catalog-.*\.db$/.test(file))
            .sort()
            .reverse();
        for (const old of backups.slice(3)) fs.rmSync(path.join(backupDirectory, old), { force: true });
        this.logger.log(`[catalogue] Backup: ${backupPath}`);
    }

    async fetchJson(url) {
        const response = await this.fetch(url, {
            headers: { Accept: 'application/json', 'User-Agent': 'DaisyCatalogueUpdater/1.0' },
            signal: AbortSignal.timeout(this.timeoutMs),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
        return response.json();
    }

    async fetchText(url) {
        const response = await this.fetch(url, {
            headers: { 'User-Agent': 'DaisyCatalogueUpdater/1.0' },
            signal: AbortSignal.timeout(this.timeoutMs),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
        return response.text();
    }

    async fetchRecentFeed(limit) {
        const entries = [];
        for (let start = 1; entries.length < limit; start += 50) {
            const url = new URL('/feeds/posts/default', this.siteUrl);
            url.search = new URLSearchParams({
                alt: 'json',
                orderby: 'updated',
                'max-results': String(Math.min(50, limit - entries.length)),
                'start-index': String(start),
            });
            const payload = await this.fetchJson(url);
            const page = (payload.feed?.entry || []).map(parseFeedEntry).filter(Boolean);
            entries.push(...page);
            if (page.length < 50) break;
        }
        return entries.slice(0, limit);
    }

    async fetchPCloudAlbum(source) {
        const attempts = [];
        for (const host of PCLOUD_HOSTS) {
            const url = new URL(`https://${host}/showpublink`);
            url.search = new URLSearchParams({ code: source.code });
            try {
                const text = await this.fetchText(url);
                const payload = parsePCloudJson(text);
                if (payload.result !== 0 || !payload.metadata) {
                    attempts.push(`${host}: ${payload.error || `result ${payload.result}`}`);
                    continue;
                }
                return {
                    code: source.code,
                    shareUrl: source.url,
                    title: payload.metadata.name || source.code,
                    files: flattenPCloudFiles(payload.metadata).map(file => ({
                        pcloudFileId: String(file.fileid),
                        title: file.name || String(file.fileid),
                        size: Number(file.size) || 0,
                        contentType: file.contenttype || null,
                        duration: Number(file.audiolength || file.duration) || null,
                    })),
                    error: null,
                };
            } catch (error) {
                attempts.push(`${host}: ${error.message}`);
            }
        }
        throw new Error(`pCloud ${source.code} failed (${attempts.join('; ')})`);
    }

    async fetchMediaFireFolder(folder) {
        const files = [];
        const queue = [folder.key];
        const visited = new Set();
        while (queue.length > 0 && visited.size < 100) {
            const folderKey = queue.shift();
            if (visited.has(folderKey)) continue;
            visited.add(folderKey);
            for (const contentType of ['files', 'folders']) {
                for (let chunk = 1; chunk <= 100; chunk += 1) {
                    const url = new URL('https://www.mediafire.com/api/1.5/folder/get_content.php');
                    url.search = new URLSearchParams({
                        folder_key: folderKey,
                        content_type: contentType,
                        chunk: String(chunk),
                        response_format: 'json',
                    });
                    const payload = await this.fetchJson(url);
                    const content = payload.response?.folder_content;
                    if (!content) break;
                    const items = contentType === 'files' ? (content.files || []) : (content.folders || []);
                    if (contentType === 'files') {
                        files.push(...items.map(file => ({
                            quickkey: file.quickkey,
                            filename: decodeRepeated(file.filename || file.quickkey),
                            url: file.links?.normal_download || null,
                        })));
                    } else {
                        queue.push(...items.map(item => item.folderkey).filter(Boolean));
                    }
                    if (items.length < Number(content.chunk_size || 100)) break;
                }
            }
        }
        return { url: folder.url, files: uniqueBy(files, file => file.quickkey) };
    }

    async fetchMediaFireSources(links) {
        const sources = [];
        for (const folder of links.mediafireFolders) {
            try {
                sources.push(await this.fetchMediaFireFolder(folder));
            } catch (error) {
                this.logger.warn(`[catalogue] MediaFire folder ${folder.key} failed: ${error.message}`);
            }
        }
        if (links.mediafireFiles.length > 0) {
            sources.push({ url: links.mediafireFiles[0].url, files: links.mediafireFiles });
        }
        return sources;
    }

    upsertPost(entry) {
        this.database.prepare(`
            INSERT INTO posts(url, title, published, source_updated, cover_url)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                published = excluded.published,
                source_updated = excluded.source_updated,
                cover_url = COALESCE(excluded.cover_url, posts.cover_url)
        `).run(entry.url, entry.title, entry.published, entry.updated, entry.coverUrl);
    }

    upsertArtist(name) {
        const cleanName = cleanDisplayTitle(name);
        if (!cleanName) return null;
        this.database.prepare(`
            INSERT INTO artists(name, search_key) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET search_key = excluded.search_key
        `).run(cleanName, PhyuCatalog.normalizeSearchText(cleanName));
        return this.database.prepare('SELECT id FROM artists WHERE name = ?').get(cleanName)?.id || null;
    }

    upsertAlbum(entry, album) {
        const displayTitle = cleanDisplayTitle(album.title);
        this.database.prepare(`
            INSERT INTO albums(post_url, pcloud_code, share_url, title, display_title, search_key, scanned_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pcloud_code) DO UPDATE SET
                post_url = excluded.post_url,
                share_url = excluded.share_url,
                title = excluded.title,
                display_title = excluded.display_title,
                search_key = excluded.search_key,
                scanned_at = excluded.scanned_at,
                error = excluded.error
        `).run(
            entry.url,
            album.code,
            album.shareUrl,
            album.title,
            displayTitle,
            PhyuCatalog.normalizeSearchText(album.title),
            new Date().toISOString(),
            album.error
        );
        const albumId = this.database.prepare('SELECT id FROM albums WHERE pcloud_code = ?').get(album.code).id;
        const fallbackArtist = extractArtistNames(entry.title)[0] || '';
        for (const file of album.files) {
            const displayTrack = cleanDisplayTitle(file.title);
            const artistText = trackArtist(file.title, fallbackArtist);
            this.database.prepare(`
                INSERT INTO tracks(album_id, pcloud_file_id, title, display_title, search_key, size, content_type, duration, artist_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(album_id, pcloud_file_id) DO UPDATE SET
                    title = excluded.title,
                    display_title = excluded.display_title,
                    search_key = excluded.search_key,
                    size = excluded.size,
                    content_type = excluded.content_type,
                    duration = COALESCE(excluded.duration, tracks.duration),
                    artist_text = excluded.artist_text
            `).run(
                albumId,
                file.pcloudFileId,
                file.title,
                displayTrack,
                PhyuCatalog.normalizeSearchText(file.title),
                file.size,
                file.contentType,
                file.duration,
                artistText
            );
            const trackId = this.database.prepare(
                'SELECT id FROM tracks WHERE album_id = ? AND pcloud_file_id = ?'
            ).get(albumId, file.pcloudFileId).id;
            const artistId = this.upsertArtist(artistText);
            if (artistId) {
                this.database.prepare(
                    'INSERT OR IGNORE INTO track_artists(track_id, artist_id, position) VALUES (?, ?, 0)'
                ).run(trackId, artistId);
            }
        }
        for (const [position, name] of extractArtistNames(entry.title).entries()) {
            const artistId = this.upsertArtist(name);
            if (artistId) {
                this.database.prepare(
                    'INSERT OR IGNORE INTO album_artists(album_id, artist_id, position) VALUES (?, ?, ?)'
                ).run(albumId, artistId, position);
            }
        }
        return albumId;
    }

    loadPostAlbums(postUrl) {
        const albums = this.database.prepare(
            'SELECT id, pcloud_code, title, mediafire_url FROM albums WHERE post_url = ? ORDER BY id'
        ).all(postUrl);
        return albums.map(album => ({
            ...album,
            tracks: this.database.prepare(
                'SELECT id, title, CAST(pcloud_file_id AS TEXT) AS pcloud_file_id FROM tracks WHERE album_id = ? ORDER BY id'
            ).all(album.id),
        }));
    }

    applyMediaFire(postUrl, sources, stats) {
        if (sources.length === 0) return;
        const albums = this.loadPostAlbums(postUrl);
        for (const album of albums) {
            let best = null;
            for (const source of sources) {
                const matches = matchMediaFireFiles(album.tracks, source.files);
                if (!best || matches.length > best.matches.length) best = { source, matches };
            }
            if (!best || best.matches.length === 0) continue;
            this.database.prepare('UPDATE albums SET mediafire_url = ? WHERE id = ?').run(best.source.url, album.id);
            for (const match of best.matches) {
                const result = this.database.prepare(
                    'UPDATE tracks SET mediafire_quick_key = ? WHERE id = ? AND (mediafire_quick_key IS NULL OR mediafire_quick_key <> ?)'
                ).run(match.file.quickkey, match.track.id, match.file.quickkey);
                stats.mediafireTracksUpdated += Number(result.changes || 0);
            }
        }
    }

    async updatePost(entry, stats) {
        const albums = [];
        for (const source of entry.links.pcloud) {
            try {
                albums.push(await this.fetchPCloudAlbum(source));
            } catch (error) {
                const existing = this.database.prepare('SELECT title FROM albums WHERE pcloud_code = ?').get(source.code);
                albums.push({
                    code: source.code,
                    shareUrl: source.url,
                    title: existing?.title || entry.title,
                    files: [],
                    error: error.message,
                });
            }
        }
        const mediafireSources = await this.fetchMediaFireSources(entry.links);
        if (this.dryRun) {
            this.logger.log(
                `[catalogue] DRY RUN ${entry.url}: ${albums.length} album(s), `
                + `${albums.reduce((total, album) => total + album.files.length, 0)} track(s), `
                + `${mediafireSources.length} MediaFire source(s)`
            );
            return;
        }

        this.database.exec('BEGIN IMMEDIATE');
        try {
            this.upsertPost(entry);
            for (const album of albums) {
                this.upsertAlbum(entry, album);
                stats.albumsUpdated += 1;
                stats.tracksUpdated += album.files.length;
            }
            this.applyMediaFire(entry.url, mediafireSources, stats);
            this.database.exec('COMMIT');
        } catch (error) {
            this.database.exec('ROLLBACK');
            throw error;
        }
    }

    async backfillMediaFire(limit, stats) {
        const posts = this.database.prepare(`
            SELECT p.url, p.title, p.published, p.source_updated, p.cover_url
            FROM posts p
            JOIN albums a ON a.post_url = p.url
            JOIN tracks t ON t.album_id = a.id
            WHERE t.mediafire_quick_key IS NULL OR trim(t.mediafire_quick_key) = ''
            GROUP BY p.url
            ORDER BY MIN(a.scanned_at), p.url
            LIMIT ?
        `).all(limit);
        for (let index = 0; index < posts.length; index += 1) {
            const post = posts[index];
            this.logger.log(`[catalogue] MediaFire ${index + 1}/${posts.length}: ${post.title}`);
            try {
                const html = await this.fetchText(post.url);
                const links = extractSourceLinks(html, post.url);
                const sources = await this.fetchMediaFireSources(links);
                if (!this.dryRun) {
                    this.database.exec('BEGIN IMMEDIATE');
                    try {
                        this.applyMediaFire(post.url, sources, stats);
                        this.database.prepare('UPDATE albums SET scanned_at = ? WHERE post_url = ?')
                            .run(new Date().toISOString(), post.url);
                        this.database.exec('COMMIT');
                    } catch (error) {
                        this.database.exec('ROLLBACK');
                        throw error;
                    }
                }
                stats.backfillPosts += 1;
            } catch (error) {
                stats.errors += 1;
                this.logger.error(`[catalogue] MediaFire backfill failed ${post.url}: ${error.message}`);
            }
        }
    }
}

module.exports = {
    CatalogueUpdater,
    cleanDisplayTitle,
    extractSourceLinks,
    matchMediaFireFiles,
    matchingKey,
    parseFeedEntry,
    parsePCloudJson,
    trackNumber,
};
