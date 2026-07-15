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
        previousTracks: [],
        shuffle: false,
        loop: false,
        autoplay: false,
        currentTrack: { title: 'Current song' },
        hasLyrics: () => false,
    };

    const rows = await manager.createControlButtons(player);
    const favoriteIds = rows[2].toJSON().components.map(component => component.custom_id);
    const firstRowIds = rows[0].toJSON().components.map(component => component.custom_id);

    assert.equal(rows.length, 3);
    assert.equal(firstRowIds[0], 'music_previous:requester-1:session-1');
    assert.equal(rows[0].toJSON().components[0].disabled, true);
    assert.deepEqual(favoriteIds, ['favorite:current-toggle:session-1']);
});

test('now-playing controls enable Previous when playback history exists', async () => {
    const manager = new MusicEmbedManager({});
    const player = {
        guild: { id: 'guild-1' },
        sessionId: 'session-1',
        requesterId: 'requester-1',
        paused: false,
        queue: [],
        previousTracks: [{ title: 'Previous song' }],
        shuffle: false,
        loop: false,
        autoplay: false,
        currentTrack: { title: 'Current song' },
        hasLyrics: () => false,
    };

    const rows = await manager.createControlButtons(player);
    const previousButton = rows[0].toJSON().components[0];

    assert.equal(previousButton.custom_id, 'music_previous:requester-1:session-1');
    assert.equal(previousButton.disabled, false);
});
