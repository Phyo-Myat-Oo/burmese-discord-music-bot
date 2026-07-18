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
    assert.match(text, /Phyu Random Catalogue/);
    assert.match(text, /YouTube/);
    assert.match(text, /နားထောင်သူတိုင်း/);
    assert.ok(json.description.length <= 4096);
    assert.ok(json.fields.every(field => field.name.length <= 256 && field.value.length <= 1024));
    const embedCharacters = json.title.length
        + json.description.length
        + json.fields.reduce((total, field) => total + field.name.length + field.value.length, 0)
        + (json.footer?.text?.length || 0);
    assert.ok(embedCharacters <= 6000);
    assert.equal(payload.components[0].components[0].data.custom_id, 'help_refresh');
});
