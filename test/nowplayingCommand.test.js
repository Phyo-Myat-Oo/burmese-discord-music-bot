const assert = require('node:assert/strict');
const test = require('node:test');
const command = require('../commands/nowplaying');

test('/nowplaying delegates to the shared interactive playback card', async () => {
    let called = false;
    const player = { currentTrack: { title: 'Playing' } };
    const interaction = { guild: { id: 'guild-1' } };
    const client = {
        players: new Map([['guild-1', player]]),
        musicEmbedManager: {
            showNowPlayingCard: async (receivedPlayer, receivedInteraction) => {
                called = receivedPlayer === player && receivedInteraction === interaction;
            }
        }
    };

    await command.execute(interaction, client);
    assert.equal(called, true);
});
