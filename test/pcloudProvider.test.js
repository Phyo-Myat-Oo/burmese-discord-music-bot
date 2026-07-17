const assert = require('node:assert/strict');
const test = require('node:test');
const { PCloudProvider, PCloudProviderError } = require('../src/providers/PCloudProvider');

const track = {
    id: 42,
    stableKey: 'phyu:track:42',
    pcloudCode: 'stable-code',
    pcloudFileId: 123456789,
};

test('accepts exact pCloud IDs larger than JavaScript safe integers', async () => {
    let requestedUrl;
    const provider = new PCloudProvider({
        fetch: async url => {
            requestedUrl = url;
            return jsonResponse({ result: 0, hosts: ['cdn.example.com'], path: '/large.mp3' });
        },
        apiHosts: ['api.pcloud.com'],
    });

    await provider.resolve({ ...track, pcloudFileId: '725200524028587006' });

    assert.equal(requestedUrl.searchParams.get('fileid'), '725200524028587006');
});

function jsonResponse(body, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
    };
}

test('resolves a fresh pCloud URL from stable catalogue identifiers', async () => {
    let requestedUrl;
    const provider = new PCloudProvider({
        fetch: async url => {
            requestedUrl = new URL(url);
            return jsonResponse({
                result: 0,
                hosts: ['c123.pcloud.com'],
                path: '/hash/My%20Song.mp3',
                expires: 'Wed, 15 Jul 2026 13:22:41 +0000',
            });
        },
    });

    const resolved = await provider.resolve(track);

    assert.equal(requestedUrl.searchParams.get('code'), track.pcloudCode);
    assert.equal(requestedUrl.searchParams.get('fileid'), String(track.pcloudFileId));
    assert.equal(resolved.source, 'pcloud');
    assert.equal(resolved.url, 'https://c123.pcloud.com/hash/My%20Song.mp3');
    assert.equal(resolved.expiresAt, 'Wed, 15 Jul 2026 13:22:41 +0000');
    assert.equal('url' in track, false);
});

test('tries the alternate pCloud data center after an API error', async () => {
    const requestedHosts = [];
    const provider = new PCloudProvider({
        fetch: async url => {
            const host = new URL(url).host;
            requestedHosts.push(host);
            if (host === 'api.pcloud.com') {
                return jsonResponse({ result: 7001, error: 'Invalid link code' });
            }
            return jsonResponse({ result: 0, hosts: ['e1.pcloud.com'], path: '/audio.mp3' });
        },
    });

    const resolved = await provider.resolve(track);
    assert.deepEqual(requestedHosts, ['api.pcloud.com', 'eapi.pcloud.com']);
    assert.equal(resolved.apiHost, 'eapi.pcloud.com');
});

test('reports all pCloud resolution attempts without mutating the track', async () => {
    const provider = new PCloudProvider({
        fetch: async () => jsonResponse({ result: 7005, error: 'Traffic limit reached' }),
    });

    await assert.rejects(
        provider.resolve(track),
        error => error instanceof PCloudProviderError && error.attempts.length === 2
    );
    assert.equal('url' in track, false);
});
