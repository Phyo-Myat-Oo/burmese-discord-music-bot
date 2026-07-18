const assert = require('node:assert/strict');
const test = require('node:test');
const { MediaFireProvider, MediaFireProviderError } = require('../src/providers/MediaFireProvider');

function jsonResponse(body, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
    };
}

function redirectResponse(location) {
    return {
        ok: false,
        status: 302,
        headers: { get: name => name.toLowerCase() === 'location' ? location : null },
    };
}

test('resolves a MediaFire direct link from a saved quick key', async () => {
    let requestedUrl;
    const track = { stableKey: 'phyu:track:42', mediafireQuickKey: 'abc123' };
    const provider = new MediaFireProvider({
        fetch: async url => {
            requestedUrl = new URL(url);
            return jsonResponse({
                response: {
                    result: 'Success',
                    links: [{ direct_download: 'https://download.example/song.mp3' }],
                },
            });
        },
    });

    const result = await provider.resolve(track);

    assert.equal(requestedUrl.searchParams.get('quick_key'), 'abc123');
    assert.equal(requestedUrl.searchParams.get('link_type'), 'direct_download');
    assert.equal(result.source, 'mediafire');
    assert.equal(result.url, 'https://download.example/song.mp3');
    assert.equal('url' in track, false);
});

test('falls back to the public MediaFire redirect when its API requires permission', async () => {
    let calls = 0;
    const provider = new MediaFireProvider({
        fetch: async () => {
            calls += 1;
            if (calls === 1) {
                return jsonResponse({
                    response: {
                        result: 'Success',
                        links: [{
                            quickkey: 'public-key',
                            direct_download_error: '45',
                            direct_download_error_message: 'Insufficient Permissions',
                        }],
                    },
                });
            }
            return redirectResponse('https://download854.mediafire.com/token/public-key/song.mp3');
        },
    });

    const result = await provider.resolve({ mediafireQuickKey: 'public-key' });

    assert.equal(calls, 2);
    assert.equal(result.source, 'mediafire-public');
    assert.equal(result.url, 'https://download854.mediafire.com/token/public-key/song.mp3');
});

test('rejects an untrusted public fallback redirect', async () => {
    let calls = 0;
    const provider = new MediaFireProvider({
        fetch: async () => {
            calls += 1;
            if (calls === 1) {
                return jsonResponse({ response: { result: 'Error', message: 'File unavailable' } });
            }
            return redirectResponse('https://malicious.example/song.mp3');
        },
    });

    await assert.rejects(
        provider.resolve({ mediafireQuickKey: 'missing' }),
        error => error instanceof MediaFireProviderError && /untrusted/i.test(error.message)
    );
});
