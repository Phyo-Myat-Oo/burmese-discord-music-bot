const { PCloudProvider } = require('./PCloudProvider');
const { MediaFireProvider } = require('./MediaFireProvider');

class PhyuPlaybackProviderError extends Error {
    constructor(message, attempts = []) {
        super(message);
        this.name = 'PhyuPlaybackProviderError';
        this.attempts = attempts;
    }
}

class PhyuPlaybackProvider {
    constructor(options = {}) {
        this.pcloud = options.pcloud || new PCloudProvider(options.pcloudOptions);
        this.mediafire = options.mediafire || new MediaFireProvider(options.mediafireOptions);
    }

    async resolve(track) {
        const catalogueTrack = this.getCatalogueTrack(track);
        const attempts = [];

        try {
            return await this.pcloud.resolve(catalogueTrack);
        } catch (error) {
            attempts.push({ source: 'pcloud', error: error.message });
        }

        if (catalogueTrack.mediafireQuickKey) {
            try {
                return await this.mediafire.resolve(catalogueTrack);
            } catch (error) {
                attempts.push({ source: 'mediafire', error: error.message });
            }
        } else {
            attempts.push({ source: 'mediafire', error: 'No MediaFire quick key is saved for this track.' });
        }

        throw new PhyuPlaybackProviderError(
            `Unable to resolve catalogue track ${catalogueTrack.stableKey || catalogueTrack.id || 'unknown'}.`,
            attempts
        );
    }

    getCatalogueTrack(track) {
        if (!track || typeof track !== 'object') {
            throw new TypeError('A catalogue track is required.');
        }

        const saved = track.extra?.catalogue || track.extra?.catalog || track;
        return {
            ...saved,
            id: saved.id || saved.trackId || track.id,
            stableKey: saved.stableKey || track.url || track.id,
        };
    }
}

module.exports = { PhyuPlaybackProvider, PhyuPlaybackProviderError };
