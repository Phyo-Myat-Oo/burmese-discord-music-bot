const DEFAULT_API_HOSTS = ['api.pcloud.com', 'eapi.pcloud.com'];
const DEFAULT_TIMEOUT_MS = 20_000;

class PCloudProviderError extends Error {
    constructor(message, attempts = []) {
        super(message);
        this.name = 'PCloudProviderError';
        this.attempts = attempts;
    }
}

class PCloudProvider {
    constructor(options = {}) {
        this.fetch = options.fetch || global.fetch;
        this.apiHosts = options.apiHosts || DEFAULT_API_HOSTS;
        this.timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;

        if (typeof this.fetch !== 'function') {
            throw new TypeError('PCloudProvider requires a fetch implementation.');
        }
    }

    async resolve(track) {
        this.validateTrack(track);
        const attempts = [];

        for (const apiHost of this.apiHosts) {
            const endpoint = new URL(`https://${apiHost}/getpublinkdownload`);
            endpoint.search = new URLSearchParams({
                code: track.pcloudCode,
                fileid: String(track.pcloudFileId),
            });

            try {
                const response = await this.fetch(endpoint, {
                    headers: { Accept: 'application/json' },
                    signal: AbortSignal.timeout(this.timeoutMs),
                });

                if (!response.ok) {
                    attempts.push({ apiHost, httpStatus: response.status });
                    continue;
                }

                const body = await response.json();
                if (body.result !== 0 || !Array.isArray(body.hosts) || !body.hosts[0] || !body.path) {
                    attempts.push({
                        apiHost,
                        result: body.result,
                        error: body.error || 'Invalid pCloud response',
                    });
                    continue;
                }

                const url = new URL(body.path, `https://${body.hosts[0]}`).toString();
                return {
                    source: 'pcloud',
                    url,
                    expiresAt: body.expires || body.expire || null,
                    apiHost,
                };
            } catch (error) {
                attempts.push({ apiHost, error: error.message });
            }
        }

        throw new PCloudProviderError(
            `Unable to resolve pCloud track ${track.stableKey || track.id || 'unknown'}.`,
            attempts
        );
    }

    validateTrack(track) {
        if (!track || typeof track !== 'object') {
            throw new TypeError('A catalogue track is required.');
        }
        if (!track.pcloudCode || typeof track.pcloudCode !== 'string') {
            throw new TypeError('Catalogue track is missing pcloudCode.');
        }
        if (
            !Number.isSafeInteger(track.pcloudFileId) &&
            !/^[1-9]\d*$/.test(String(track.pcloudFileId || ''))
        ) {
            throw new TypeError('Catalogue track has an invalid pcloudFileId.');
        }
    }
}

module.exports = { PCloudProvider, PCloudProviderError };
