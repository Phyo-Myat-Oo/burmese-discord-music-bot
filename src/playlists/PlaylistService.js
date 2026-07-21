const DaisyStateStore = require('../state/DaisyStateStore');
const { FavoriteService } = require('../favorites/FavoriteService');
const { getPhyuCatalogClient } = require('../catalog/PhyuAutocomplete');

const MAX_PLAYLISTS_PER_SCOPE = 25;
const MAX_TRACKS_PER_PLAYLIST = 50;
const VALID_SCOPES = new Set(['personal', 'server']);

function normalizeName(value) {
    return String(value || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
}

function nameKey(value) {
    return normalizeName(value).toLocaleLowerCase('en-US');
}

function shuffleTracks(tracks, random = Math.random) {
    const shuffled = [...tracks];
    for (let index = shuffled.length - 1; index > 0; index -= 1) {
        const target = Math.floor(random() * (index + 1));
        [shuffled[index], shuffled[target]] = [shuffled[target], shuffled[index]];
    }
    return shuffled;
}

class PlaylistService {
    constructor(options = {}) {
        this.store = options.store || new DaisyStateStore();
        this.catalogue = options.catalogue || getPhyuCatalogClient();
        this.trackCodec = options.trackCodec || new FavoriteService({
            store: this.store,
            catalogue: this.catalogue,
        });
        this.random = options.random || Math.random;
    }

    scopeId(scopeType, context) {
        if (!VALID_SCOPES.has(scopeType)) throw new TypeError('Playlist scope must be personal or server.');
        const value = scopeType === 'personal' ? context?.userId : context?.guildId;
        const normalized = String(value || '').trim();
        if (!normalized) throw new TypeError(`${scopeType} playlist scope is unavailable here.`);
        return normalized;
    }

    validateName(value) {
        const name = normalizeName(value);
        if (!name || name.length > 50) {
            throw new TypeError('Playlist name must contain between 1 and 50 characters.');
        }
        return { name, nameKey: nameKey(name) };
    }

    listPlaylists(scopeType, context) {
        return this.store.listPlaylists(scopeType, this.scopeId(scopeType, context), {
            limit: MAX_PLAYLISTS_PER_SCOPE,
        });
    }

    createPlaylist(scopeType, context, value) {
        const scopeId = this.scopeId(scopeType, context);
        const { name, nameKey: normalizedKey } = this.validateName(value);
        const existing = this.store.listPlaylists(scopeType, scopeId, {
            limit: MAX_PLAYLISTS_PER_SCOPE,
        });
        if (existing.some(playlist => playlist.nameKey === normalizedKey)) {
            throw new Error(`A playlist named “${name}” already exists in this scope.`);
        }
        if (existing.length >= MAX_PLAYLISTS_PER_SCOPE) {
            throw new Error(`This scope already has the maximum of ${MAX_PLAYLISTS_PER_SCOPE} playlists.`);
        }
        return this.store.createPlaylist({
            scopeType,
            scopeId,
            createdBy: String(context.userId),
            name,
            nameKey: normalizedKey,
        });
    }

    getAccessiblePlaylist(playlistId, context) {
        const playlist = this.store.getPlaylistById(playlistId);
        if (!playlist) throw new Error('That playlist no longer exists.');
        const allowed = playlist.scopeType === 'personal'
            ? playlist.scopeId === String(context?.userId || '')
            : playlist.scopeId === String(context?.guildId || '');
        if (!allowed) throw new Error('You cannot access that playlist here.');
        return playlist;
    }

    renamePlaylist(playlistId, context, value) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        const { name, nameKey: normalizedKey } = this.validateName(value);
        const duplicate = this.listPlaylists(playlist.scopeType, context)
            .some(candidate => candidate.id !== playlist.id && candidate.nameKey === normalizedKey);
        if (duplicate) throw new Error(`A playlist named “${name}” already exists in this scope.`);
        return this.store.renamePlaylist(playlist.id, name, normalizedKey);
    }

    deletePlaylist(playlistId, context) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        return this.store.deletePlaylist(playlist.id);
    }

    addCurrentTrack(playlistId, context, track) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        if (!track) throw new Error('Daisy is not currently playing a song.');
        if (this.store.countPlaylistTracks(playlist.id) >= MAX_TRACKS_PER_PLAYLIST) {
            throw new Error(`This playlist already has the maximum of ${MAX_TRACKS_PER_PLAYLIST} tracks.`);
        }
        const { sourceType, sourceKey } = this.trackCodec.identifyTrack(track);
        if (this.store.hasPlaylistTrack(playlist.id, sourceType, sourceKey)) {
            throw new Error(`“${track.title}” is already in this playlist.`);
        }
        const playback = this.trackCodec.sanitizePlaybackTrack(track, sourceType);
        return this.store.addPlaylistTrack({
            playlistId: playlist.id,
            sourceType,
            sourceKey,
            title: track.title,
            artist: track.artist || null,
            album: track.album || null,
            sourceUrl: track.sourceUrl || (sourceKey.startsWith('http') ? sourceKey : null),
            duration: track.duration || 0,
            thumbnail: track.thumbnail || null,
            playbackJson: JSON.stringify(playback),
            addedBy: String(context.userId),
        });
    }

    captureTrack(track) {
        const { sourceType, sourceKey } = this.trackCodec.identifyTrack(track);
        return {
            ...this.trackCodec.sanitizePlaybackTrack(track, sourceType),
            url: sourceKey,
            sourceUrl: track.sourceUrl || (sourceKey.startsWith('http') ? sourceKey : null),
        };
    }

    listTracks(playlistId, context, options = {}) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        return this.store.listPlaylistTracks(playlist.id, options);
    }

    removeTrack(playlistId, trackId, context) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        if (!this.store.getPlaylistTrackById(playlist.id, trackId)) {
            throw new Error('That playlist track no longer exists.');
        }
        return this.store.removePlaylistTrack(playlist.id, trackId);
    }

    moveTrack(playlistId, trackId, direction, context) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        if (!['up', 'down'].includes(direction)) throw new TypeError('Track direction must be up or down.');
        if (!this.store.getPlaylistTrackById(playlist.id, trackId)) {
            throw new Error('That playlist track no longer exists.');
        }
        return this.store.movePlaylistTrack(playlist.id, trackId, direction);
    }

    async resolveForPlayback(playlistId, context, options = {}) {
        const playlist = this.getAccessiblePlaylist(playlistId, context);
        const savedTracks = this.store.listAllPlaylistTracks(playlist.id);
        const resolved = [];
        const skipped = [];
        for (const saved of savedTracks) {
            try {
                resolved.push(await this.trackCodec.resolveFavorite(saved));
            } catch (error) {
                skipped.push({ track: saved, error: error.message });
            }
        }
        return {
            playlist,
            tracks: options.shuffle ? shuffleTracks(resolved, this.random) : resolved,
            skipped,
        };
    }
}

let sharedService;
function getPlaylistService() {
    if (!sharedService) sharedService = new PlaylistService();
    return sharedService;
}

module.exports = {
    MAX_PLAYLISTS_PER_SCOPE,
    MAX_TRACKS_PER_PLAYLIST,
    PlaylistService,
    getPlaylistService,
    nameKey,
    normalizeName,
    shuffleTracks,
};
