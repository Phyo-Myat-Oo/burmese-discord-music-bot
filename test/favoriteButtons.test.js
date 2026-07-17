const assert = require('node:assert/strict');
const test = require('node:test');
const MusicEmbedManager = require('../src/MusicEmbedManager');

test('adds one Favorite toggle action to the now-playing card', async () => {
    const manager = new MusicEmbedManager({});
    const player = {
        guild: { id: 'guild-1' },
        sessionId: 'session-1',
        requesterId: 'requester-1',
        paused: false,
        queue: [],
        shuffle: false,
        loop: false,
        autoplay: false,
        currentTrack: { title: 'Current song' },
        hasLyrics: () => false,
    };

    const rows = await manager.createControlButtons(player);
    const favoriteIds = rows[2].toJSON().components.map(component => component.custom_id);

    assert.equal(rows.length, 3);
    assert.deepEqual(favoriteIds, ['favorite:current-toggle:session-1']);
});
