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

test('rejects a MediaFire response without a direct download link', async () => {
    const provider = new MediaFireProvider({
        fetch: async () => jsonResponse({ response: { result: 'Error', message: 'File unavailable' } }),
    });

    await assert.rejects(
        provider.resolve({ mediafireQuickKey: 'missing' }),
        error => error instanceof MediaFireProviderError && /unavailable/i.test(error.message)
    );
});
