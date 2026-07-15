const assert = require('node:assert/strict');
const test = require('node:test');
const {
    PhyuPlaybackProvider,
    PhyuPlaybackProviderError,
} = require('../src/providers/PhyuPlaybackProvider');

const musicTrack = {
    id: 'phyu:track:42',
    url: 'phyu:track:42',
    extra: {
        catalogue: {
            trackId: 42,
            pcloudCode: 'stable-code',
            pcloudFileId: 123,
            mediafireQuickKey: 'fallback-key',
        },
    },
};

test('uses pCloud as the primary catalogue playback source', async () => {
    let mediafireCalled = false;
    const provider = new PhyuPlaybackProvider({
        pcloud: { resolve: async () => ({ source: 'pcloud', url: 'https://pcloud.example/audio' }) },
        mediafire: { resolve: async () => { mediafireCalled = true; } },
    });

    const result = await provider.resolve(musicTrack);
    assert.equal(result.source, 'pcloud');
    assert.equal(mediafireCalled, false);
});

test('falls back to MediaFire when pCloud resolution fails', async () => {
    let receivedTrack;
    const provider = new PhyuPlaybackProvider({
        pcloud: { resolve: async () => { throw new Error('pCloud unavailable'); } },
        mediafire: {
            resolve: async track => {
                receivedTrack = track;
                return { source: 'mediafire', url: 'https://mediafire.example/audio' };
            },
        },
    });

    const result = await provider.resolve(musicTrack);
    assert.equal(result.source, 'mediafire');
    assert.equal(receivedTrack.pcloudCode, 'stable-code');
    assert.equal(receivedTrack.mediafireQuickKey, 'fallback-key');
    assert.equal('url' in musicTrack.extra.catalogue, false);
});

test('reports both provider failures without storing a temporary URL', async () => {
    const provider = new PhyuPlaybackProvider({
        pcloud: { resolve: async () => { throw new Error('pCloud unavailable'); } },
        mediafire: { resolve: async () => { throw new Error('MediaFire unavailable'); } },
    });

    await assert.rejects(
        provider.resolve(musicTrack),
        error => error instanceof PhyuPlaybackProviderError && error.attempts.length === 2
    );
    assert.equal(musicTrack.url, 'phyu:track:42');
});
