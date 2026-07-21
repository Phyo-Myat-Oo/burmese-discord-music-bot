const assert = require('node:assert/strict');
const test = require('node:test');
const { MessageFlags } = require('discord.js');
const command = require('../commands/playlists');

test('registers playlists as the only playlist slash command and opens privately', async () => {
    const json = command.data.toJSON();
    assert.equal(json.name, 'playlists');
    assert.equal(json.options?.length || 0, 0);

    let response;
    const browser = {
        createSession: () => ({ id: 'session-1' }),
        renderHome: () => ({ embeds: [{}], components: [{}] }),
    };
    await command.execute({
        user: { id: 'command-user' },
        guild: { id: 'command-guild' },
        client: { playlistBrowser: browser },
        reply: payload => { response = payload; },
    });
    assert.equal(response.flags, MessageFlags.Ephemeral);
    assert.ok(response.embeds.length > 0);
    assert.ok(response.components.length > 0);
});
