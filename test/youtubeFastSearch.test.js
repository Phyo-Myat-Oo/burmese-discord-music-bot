const test = require('node:test');
const assert = require('node:assert/strict');

const YouTube = require('../src/YouTube');

test('builds the YouTube result picker without per-video metadata requests', async () => {
    const originalGetInfo = YouTube.getInfo;
    let metadataRequests = 0;
    YouTube.getInfo = async () => {
        metadataRequests += 1;
        throw new Error('Search results must not request full video metadata');
    };

    try {
        const entries = Array.from({ length: 9 }, (_, index) => ({
            id: `video-${index}`,
            title: `Result ${index + 1}`,
            channel: 'Daisy Test Channel',
        }));

        const tracks = await YouTube.buildSearchTracks(entries, 9);

        assert.equal(tracks.length, 9);
        assert.equal(metadataRequests, 0);
        assert.equal(tracks[0].duration, 0);
        assert.equal(tracks[0].url, 'https://www.youtube.com/watch?v=video-0');
    } finally {
        YouTube.getInfo = originalGetInfo;
    }
});
