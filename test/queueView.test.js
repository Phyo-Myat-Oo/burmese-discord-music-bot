const assert = require('node:assert/strict');
const test = require('node:test');
const { createQueueView } = require('../src/QueueView');

function makePlayer(queueLength = 42) {
    return {
        sessionId: 'session-1',
        requesterId: 'requester-1',
        currentTrack: { title: 'Current song', url: 'phyu:track:1', sourceUrl: 'https://example.com/current' },
        queue: Array.from({ length: queueLength }, (_, index) => ({ title: `Queued song ${index + 1}` }))
    };
}

test('queue view paginates songs and offers a back and clear action', () => {
    const view = createQueueView(makePlayer(), { page: 1 });
    const json = view.components.map(row => row.toJSON());

    assert.equal(view.page, 1);
    assert.equal(json[0].components[0].options.length, 20);
    assert.match(json.at(-1).components[2].custom_id, /^music_queue_clear:/);
    assert.match(json.at(-1).components[3].custom_id, /^music_queue_back:/);
});

test('selecting a queued song adds a destination dropdown', () => {
    const view = createQueueView(makePlayer(), { page: 1, selectedIndex: 25 });
    const json = view.components.map(row => row.toJSON());

    assert.equal(json.length, 3);
    assert.match(json[1].components[0].custom_id, /^music_queue_move:/);
    assert.ok(json[1].components[0].options.some(option => option.value === '0'));
    assert.ok(json[1].components[0].options.some(option => option.value === '41'));
});

test('empty queue still provides Back while disabling Clear Queue', () => {
    const view = createQueueView(makePlayer(0));
    const controls = view.components[0].toJSON().components;

    assert.equal(view.components.length, 1);
    assert.equal(controls[2].disabled, true);
    assert.equal(controls[3].label, 'Back');
});
