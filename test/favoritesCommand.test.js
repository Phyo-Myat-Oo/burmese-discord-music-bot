const assert = require('node:assert/strict');
const test = require('node:test');
const command = require('../commands/favorites');

test('registers add, remove, play, and list favorites commands', () => {
    const json = command.data.toJSON();
    assert.equal(json.name, 'favorites');
    assert.deepEqual(json.options.map(option => option.name), ['add', 'remove', 'play', 'list']);
    assert.equal(json.options[0].options[0].name, 'query');
    assert.equal(json.options[0].options[0].autocomplete, true);
    assert.equal(json.options[1].options[0].name, 'query');
    assert.equal(json.options[1].options[0].autocomplete, true);
    assert.equal(json.options[2].options[0].name, 'query');
    assert.equal(json.options[2].options[0].autocomplete, true);
});

test('suggests Phyu tracks while typing favorites add', async () => {
    let choices;
    const interaction = {
        options: {
            getSubcommand: () => 'add',
            getFocused: () => 'နွေ',
        },
        respond: async payload => { choices = payload; },
    };

    await command.autocomplete(interaction);

    assert.equal(choices.length, 25);
    assert.ok(choices.every(choice => choice.name.startsWith('[Phyu Track]')));
    assert.ok(choices.every(choice => /^phyu:track:\d+$/.test(choice.value)));
});
