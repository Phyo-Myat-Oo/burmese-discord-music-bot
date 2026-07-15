const assert = require('node:assert/strict');
const test = require('node:test');
const MusicEmbedManager = require('../src/MusicEmbedManager');

test('now-playing card shows album, requester, progress, and repeat mode', async () => {
    const manager = new MusicEmbedManager({});
    const player = {
        guild: { id: 'guild-1' },
        paused: false,
        pauseReasons: new Set(),
        queue: [{ title: 'Queued song' }],
        loop: 'queue',
        getCurrentTime: () => 65_000,
    };
    const track = {
        title: 'Current song',
        url: 'https://example.com/current',
        sourceUrl: 'https://example.com/source',
        artist: 'Test artist',
        album: 'Test album',
        duration: 180,
        platform: 'phyu',
        requesterId: 'user-1',
    };

    const embed = await manager.createNowPlayingEmbed(player, track, 'guild-1');
    const fields = embed.data.fields;

    assert.ok(fields.some(field => field.name.includes('Album') && field.value === 'Test album'));
    assert.ok(fields.some(field => field.name.includes('Requested') && field.value === '<@user-1>'));
    assert.ok(fields.some(field => field.name.includes('Progress') && field.value.includes('1:05')));
    assert.ok(fields.some(field => field.name === 'Repeat' && field.value === 'Repeat: Queue'));
});
