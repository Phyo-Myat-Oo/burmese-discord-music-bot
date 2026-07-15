const assert = require('node:assert/strict');
const test = require('node:test');
const event = require('../events/buttonHandler');

function authorizedInteraction(values = []) {
    let updated;
    return {
        values,
        member: {
            id: 'requester-1',
            permissions: { has: () => false },
            roles: { cache: { some: () => false } }
        },
        update: async payload => { updated = payload; },
        reply: async () => {},
        getUpdated: () => updated
    };
}

function playerWithQueue() {
    return {
        sessionId: 'session-1',
        requesterId: 'requester-1',
        currentTrack: { title: 'Playing', url: 'https://example.com/playing' },
        queue: [{ title: 'One' }, { title: 'Two' }, { title: 'Three' }],
        clearQueue() { this.queue = []; return 3; },
        moveInQueue(from, to) {
            if (!this.queue[from] || !this.queue[to]) return false;
            const [track] = this.queue.splice(from, 1);
            this.queue.splice(to, 0, track);
            return true;
        }
    };
}

test('Clear Queue leaves the currently playing song intact', async () => {
    const interaction = authorizedInteraction();
    const player = playerWithQueue();

    await event.handleQueueClear(interaction, player, 'requester-1');

    assert.equal(player.queue.length, 0);
    assert.equal(player.currentTrack.title, 'Playing');
    assert.ok(interaction.getUpdated());
});

test('destination selection moves a queued song to the chosen position', async () => {
    const interaction = authorizedInteraction(['0']);
    const player = playerWithQueue();

    await event.handleQueueMove(
        interaction,
        player,
        'requester-1',
        'session-1',
        ['music_queue_move', 'requester-1', 'session-1', '0', '2']
    );

    assert.deepEqual(player.queue.map(track => track.title), ['Three', 'One', 'Two']);
});
