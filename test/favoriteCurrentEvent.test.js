const assert = require('node:assert/strict');
const test = require('node:test');
const event = require('../events/favoriteBrowser');

test('rejects favorite buttons from a stale now-playing card', async () => {
    let reply;
    const interaction = {
        customId: 'favorite:current-toggle:old-session',
        user: { id: 'user-1' },
        guild: { id: 'guild-1' },
        client: {
            players: new Map([['guild-1', {
                sessionId: 'current-session',
                currentTrack: { title: 'Current track' },
            }]]),
        },
        isButton: () => true,
        isStringSelectMenu: () => false,
        reply: async payload => { reply = payload; return payload; },
    };

    await event.execute(interaction);

    assert.match(reply.content, /no longer current/);
});
