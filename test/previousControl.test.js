const assert = require('node:assert/strict');
const test = require('node:test');
const event = require('../events/buttonHandler');

test('Previous control starts the previous song and refreshes the shared card', async () => {
    let replyPayload;
    let refreshed = false;
    let previousCalled = false;
    const player = {
        currentTrack: { title: 'Current song' },
        previousTracks: [{ title: 'Previous song' }],
        previous: async () => {
            previousCalled = true;
            player.currentTrack = { title: 'Previous song' };
            return true;
        },
    };
    const interaction = {
        guild: { id: 'guild-1' },
        member: {
            id: 'requester-1',
            permissions: { has: () => false },
            roles: { cache: { some: () => false } },
        },
        client: {
            musicEmbedManager: {
                updateNowPlayingEmbed: async receivedPlayer => {
                    assert.equal(receivedPlayer, player);
                    refreshed = true;
                },
            },
        },
        reply: async payload => {
            replyPayload = payload;
        },
    };

    await event.handlePrevious(interaction, player, 'requester-1');

    assert.equal(previousCalled, true);
    assert.equal(refreshed, true);
    assert.match(replyPayload.content, /previous/i);
});
