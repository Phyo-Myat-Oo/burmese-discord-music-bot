const DaisyStateStore = require('../state/DaisyStateStore');
const PhyuCatalog = require('../catalog/PhyuCatalog');
const { toMusicTrack } = require('../catalog/PhyuPlayback');

const SUPPORTED_SOURCES = new Set(['phyu', 'youtube', 'spotify', 'soundcloud', 'direct']);

class FavoriteService {
    constructor(options = {}) {
        this.store = options.store || new DaisyStateStore();
        this.catalogue = options.catalogue || new PhyuCatalog();
    }

    identifyTrack(track) {
        if (!track || typeof track !== 'object') throw new TypeError('A current track is required.');
        const sourceType = String(track.platform || 'direct').toLowerCase();
        if (!SUPPORTED_SOURCES.has(sourceType)) {
            throw new TypeError(`Favorites do not support the ${sourceType} source yet.`);
        }

        const sourceKey = sourceType === 'phyu'
            ? track.url
            : track.url || track.youtubeUrl || track.spotifyUrl || track.soundcloudUrl;
        if (!sourceKey || !track.title) throw new TypeError('The current track has no stable identity.');
        return { sourceType, sourceKey };
    }

    addFavorite(userId, track) {
        const { sourceType, sourceKey } = this.identifyTrack(track);
        const playback = this.sanitizePlaybackTrack(track, sourceType);
        return this.store.upsertFavorite({
            userId: String(userId),
            sourceType,
            sourceKey,
            title: track.title,
            artist: track.artist || null,
            album: track.album || null,
            sourceUrl: track.sourceUrl || (sourceKey.startsWith('http') ? sourceKey : null),
            duration: track.duration || 0,
            thumbnail: track.thumbnail || null,
            playbackJson: JSON.stringify(playback),
        });
    }

    removeFavorite(userId, track) {
        const { sourceType, sourceKey } = this.identifyTrack(track);
        return this.store.removeFavorite(String(userId), sourceType, sourceKey);
    }

    isFavorite(userId, track) {
        const { sourceType, sourceKey } = this.identifyTrack(track);
        return this.store.hasFavorite(String(userId), sourceType, sourceKey);
    }

    toggleFavorite(userId, track) {
        if (this.isFavorite(userId, track)) {
            this.removeFavorite(userId, track);
            return { favorited: false };
        }
        const result = this.addFavorite(userId, track);
        return { favorited: true, favorite: result.favorite };
    }

    removeFavoriteById(userId, favoriteId) {
        return this.store.removeFavoriteById(String(userId), favoriteId);
    }

    listFavorites(userId, options = {}) {
        return this.store.listFavorites(String(userId), options);
    }

    countFavorites(userId) {
        return this.store.countFavorites(String(userId));
    }

    searchFavorites(userId, query, options = {}) {
        return this.store.searchFavorites(String(userId), query, options);
    }

    getFavorite(userId, favoriteId) {
        return this.store.getFavoriteById(String(userId), favoriteId);
    }

    resolveFavorite(favorite) {
        if (!favorite) throw new Error('Favorite not found.');
        if (favorite.sourceType === 'phyu') {
            const trackId = /^phyu:track:(\d+)$/.exec(favorite.sourceKey)?.[1];
            if (!trackId) throw new Error('The saved Phyu favorite has an invalid stable key.');
            const catalogueTrack = this.catalogue.getTrackById(trackId);
            if (!catalogueTrack) throw new Error('The saved Phyu track is no longer in the catalogue.');
            return toMusicTrack(catalogueTrack);
        }

        const track = JSON.parse(favorite.playbackJson);
        if (!track.url || !track.platform || !track.title) {
            throw new Error('The saved favorite is missing stable playback data.');
        }
        return track;
    }

    sanitizePlaybackTrack(track, sourceType) {
        return {
            id: track.id || null,
            title: track.title,
            url: track.url,
            sourceUrl: track.sourceUrl || null,
            duration: Math.max(0, Math.floor(Number(track.duration) || 0)),
            thumbnail: track.thumbnail || null,
            artist: track.artist || null,
            album: track.album || null,
            platform: sourceType,
            type: track.type || 'track',
            isLive: Boolean(track.isLive || track.live),
            extra: sourceType === 'phyu' ? track.extra || null : null,
        };
    }
}

let sharedService;
function getFavoriteService() {
    if (!sharedService) sharedService = new FavoriteService();
    return sharedService;
}

module.exports = { FavoriteService, getFavoriteService };
