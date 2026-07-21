const assert = require('node:assert/strict');
const test = require('node:test');
const MusicEmbedManager = require('../src/MusicEmbedManager');

test('organizes now-playing controls into predictable functional rows', async () => {
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
    };

    const rows = await manager.createControlButtons(player);
    const rowIds = rows.map(row =>
        row.toJSON().components.map(component => component.custom_id.split(':')[0])
    );

    assert.equal(rows.length, 4);
    assert.deepEqual(rowIds, [
        ['music_previous', 'music_pause', 'music_skip'],
        ['music_stop', 'music_queue'],
        ['music_shuffle', 'music_loop', 'music_autoplay'],
        ['music_volume', 'favorite', 'playlist'],
    ]);
    assert.equal(
        rows[3].toJSON().components[1].custom_id,
        'favorite:current-toggle:session-1'
    );
    assert.equal(
        rows[3].toJSON().components[2].custom_id,
        'playlist:current:session-1'
    );
});
