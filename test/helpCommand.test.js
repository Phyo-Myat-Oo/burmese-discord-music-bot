const test = require('node:test');
const assert = require('node:assert/strict');

const helpCommand = require('../commands/help');

const client = {
    user: {
        username: 'Daisy',
        displayAvatarURL: () => 'https://example.com/daisy.png',
    },
};

test('registers the Burmese help command', () => {
    const command = helpCommand.data.toJSON();
    assert.equal(command.name, 'help');
    assert.match(command.description, /မြန်မာ/);
});

test('lists every current public command in Burmese help', () => {
    const payload = helpCommand.buildHelpPayload(client);
    const json = payload.embeds[0].toJSON();
    const text = [json.title, json.description, ...json.fields.flatMap(field => [field.name, field.value])].join('\n');

    assert.match(text, /Daisy Music Bot အသုံးပြုနည်း/);
    assert.match(text, /\/phyu play/);
    assert.match(text, /\/phyu search/);
    assert.match(text, /\/youtube play/);
    assert.match(text, /\/youtube search/);
    assert.match(text, /\/favorites add/);
    assert.match(text, /\/favorites list/);
    assert.match(text, /\/favorites play/);
    assert.match(text, /\/favorites remove/);
    assert.match(text, /\/nowplaying/);
    assert.match(text, /\/help/);
    assert.equal(payload.components[0].components[0].data.custom_id, 'help_refresh');
});
