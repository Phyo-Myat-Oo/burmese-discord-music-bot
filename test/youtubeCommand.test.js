const assert = require('node:assert/strict');
const test = require('node:test');
const YouTubeSearchCommand = require('../src/YouTubeSearchCommand');
const command = require('../commands/youtube');

test('registers one YouTube command with play and search subcommands', () => {
    const json = command.data.toJSON();

    assert.equal(json.name, 'youtube');
    assert.deepEqual(json.options.map(option => option.name), ['play', 'search']);
    assert.ok(json.options.every(option => option.options[0].name === 'query'));
    assert.ok(json.options.every(option => option.options[0].required === true));
});

test('delegates /youtube search to the result-picker flow', async t => {
    const originalExecute = YouTubeSearchCommand.execute;
    let delegated;
    YouTubeSearchCommand.execute = async (interaction, client) => {
        delegated = { interaction, client };
        return 'search-result';
    };
    t.after(() => {
        YouTubeSearchCommand.execute = originalExecute;
    });

    const interaction = { options: { getSubcommand: () => 'search' } };
    const client = { id: 'client' };
    const result = await command.execute(interaction, client);

    assert.equal(result, 'search-result');
    assert.deepEqual(delegated, { interaction, client });
});
