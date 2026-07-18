const assert = require('node:assert/strict');
const test = require('node:test');
const buttonHandler = require('../events/buttonHandler');

test('allows every listener to use playback controls', () => {
    const interaction = {
        member: {
            id: 'listener-who-did-not-start-music',
            permissions: { has: () => false },
            roles: { cache: new Map() },
        },
    };

    assert.equal(buttonHandler.isAuthorized(interaction, 'different-music-starter'), true);
});
