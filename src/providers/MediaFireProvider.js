const DEFAULT_API_HOST = 'www.mediafire.com';
const DEFAULT_TIMEOUT_MS = 20_000;

class MediaFireProviderError extends Error {
    constructor(message, details = {}) {
        super(message);
        this.name = 'MediaFireProviderError';
        this.details = details;
    }
}

class MediaFireProvider {
    constructor(options = {}) {
        this.fetch = options.fetch || global.fetch;
        this.apiHost = options.apiHost || DEFAULT_API_HOST;
        this.timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;

        if (typeof this.fetch !== 'function') {
            throw new TypeError('MediaFireProvider requires a fetch implementation.');
        }
    }

    async resolve(track) {
        this.validateTrack(track);

        const endpoint = new URL(`https://${this.apiHost}/api/1.5/file/get_links.php`);
        endpoint.search = new URLSearchParams({
            quick_key: track.mediafireQuickKey,
            link_type: 'direct_download',
            response_format: 'json',
        });

        try {
            const response = await this.fetch(endpoint, {
                headers: { Accept: 'application/json' },
                signal: AbortSignal.timeout(this.timeoutMs),
            });

            if (!response.ok) {
                throw new MediaFireProviderError(
                    `MediaFire returned HTTP ${response.status}.`,
                    { httpStatus: response.status }
                );
            }

            const body = await response.json();
            const apiResponse = body?.response || body;
            const link = apiResponse?.links?.find(candidate => candidate?.direct_download);
            const succeeded = apiResponse?.result === 'Success' || apiResponse?.result === 0;

            if (!succeeded || !link?.direct_download) {
                throw new MediaFireProviderError(
                    apiResponse?.message || apiResponse?.error || 'MediaFire did not return a direct download link.',
                    { result: apiResponse?.result ?? null }
                );
            }

            return {
                source: 'mediafire',
                url: new URL(link.direct_download).toString(),
                apiHost: this.apiHost,
            };
        } catch (error) {
            if (error instanceof MediaFireProviderError) throw error;
            throw new MediaFireProviderError(
                `Unable to resolve MediaFire track ${track.stableKey || track.id || 'unknown'}.`,
                { error: error.message }
            );
        }
    }

    validateTrack(track) {
        if (!track || typeof track !== 'object') {
            throw new TypeError('A catalogue track is required.');
        }
        if (!track.mediafireQuickKey || typeof track.mediafireQuickKey !== 'string') {
            throw new TypeError('Catalogue track is missing mediafireQuickKey.');
        }
    }
}

module.exports = { MediaFireProvider, MediaFireProviderError };
