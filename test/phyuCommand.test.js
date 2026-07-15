const assert = require('node:assert/strict');
const test = require('node:test');
const command = require('../commands/phyu');

test('registers search plus autocomplete-based quick play', () => {
    const json = command.data.toJSON();

    assert.equal(json.name, 'phyu');
    assert.equal(json.options.length, 2);
    assert.equal(json.options[0].name, 'search');
    assert.equal(json.options[0].options[0].name, 'query');
    assert.equal(json.options[1].name, 'play');
    assert.equal(json.options[1].options[0].name, 'query');
    assert.equal(json.options[1].options[0].autocomplete, true);
});

test('returns exact catalogue suggestions while the user types', async () => {
    let choices;
    const interaction = {
        options: { getFocused: () => 'နွေ' },
        respond: async payload => { choices = payload; },
    };

    await command.autocomplete(interaction);

    assert.equal(choices.length, 25);
    assert.ok(choices.every(choice => choice.name.length <= 100));
    assert.ok(choices.some(choice => choice.name.startsWith('[Track]')));
    assert.ok(choices.some(choice => choice.name.startsWith('[Album]')));
    assert.ok(choices.every(choice => /^phyu:(track|album):\d+$/.test(choice.value)));
});

test('quick play queues only the autocomplete suggestion selected by the user', async () => {
    let queuedData;
    const voiceChannel = {
        id: 'voice-1',
        permissionsFor: () => ({ has: () => true }),
    };
    const client = {
        players: new Map([['guild-1', {}]]),
        musicEmbedManager: {
            handleMusicData: async (_guildId, trackData) => {
                queuedData = trackData;
                return { success: true };
            },
        },
    };
    const interaction = {
        options: {
            getSubcommand: () => 'play',
            getString: () => 'phyu:track:1162',
        },
        member: { voice: { channel: voiceChannel } },
        guild: { id: 'guild-1', members: { me: { voice: { channel: null } } } },
        channel: { id: 'text-1' },
        deferReply: async () => {},
        editReply: async payload => payload,
    };

    await command.execute(interaction, client);

    assert.equal(queuedData.tracks.length, 1);
    assert.equal(queuedData.tracks[0].url, 'phyu:track:1162');
});

test('quick play queues the full album when an album suggestion is selected', async () => {
    let queuedData;
    const voiceChannel = { id: 'voice-1', permissionsFor: () => ({ has: () => true }) };
    const client = {
        players: new Map([['guild-1', {}]]),
        musicEmbedManager: {
            handleMusicData: async (_guildId, trackData) => {
                queuedData = trackData;
                return { success: true };
            },
        },
    };
    const interaction = {
        options: {
            getSubcommand: () => 'play',
            getString: () => 'phyu:album:1',
        },
        member: { voice: { channel: voiceChannel } },
        guild: { id: 'guild-1', members: { me: { voice: { channel: null } } } },
        channel: { id: 'text-1' },
        deferReply: async () => {},
        editReply: async payload => payload,
    };

    await command.execute(interaction, client);

    assert.equal(queuedData.isPlaylist, true);
    assert.ok(queuedData.tracks.length > 1);
    assert.ok(queuedData.tracks.every(track => track.album === queuedData.tracks[0].album));
});

test('does not guess when quick play is submitted without choosing a suggestion', async () => {
    let reply;
    const interaction = {
        options: {
            getSubcommand: () => 'play',
            getString: () => 'နွေဦးဝေဒနာ',
        },
        deferReply: async () => {},
        editReply: async payload => { reply = payload; return payload; },
    };

    await command.execute(interaction, {});
    assert.match(reply.content, /choose one of Daisy’s suggestions/);
});

test('keeps the paginated search picker as the advanced flow', async () => {
    let reply;
    const interaction = {
        user: { id: 'search-user' },
        options: {
            getSubcommand: () => 'search',
            getString: () => 'နွေ',
        },
        deferReply: async () => {},
        editReply: async payload => { reply = payload; return payload; },
    };

    await command.execute(interaction);

    assert.equal(reply.components.length, 4);
    const select = reply.components[0].toJSON().components[0];
    const artistSelect = reply.components[1].toJSON().components[0];
    const randomButtons = reply.components[2].toJSON().components;
    assert.equal(select.options.length, 25);
    assert.match(select.custom_id, /^phyu:search-item-select:/);
    assert.ok(select.options.some(option => option.label.startsWith('[Track]')));
    assert.ok(select.options.some(option => option.label.startsWith('[Album]')));
    assert.match(artistSelect.custom_id, /^phyu:artist-select:/);
    assert.equal(randomButtons[0].custom_id.startsWith('phyu:random-track:'), true);
    assert.equal(randomButtons[1].custom_id.startsWith('phyu:random-album:'), true);
});

test('converts a catalogue row into a stable MusicBot track', () => {
    const musicTrack = command.toMusicTrack({
        id: 42,
        stableKey: 'phyu:track:42',
        title: 'Test song',
        artist: 'Test artist',
        album: 'Test album',
        postUrl: 'https://example.com/post',
        coverUrl: 'https://example.com/cover.jpg',
        pcloudCode: 'stable-code',
        pcloudFileId: 123,
        mediafireQuickKey: 'fallback-key',
    });

    assert.equal(musicTrack.platform, 'phyu');
    assert.equal(musicTrack.url, 'phyu:track:42');
    assert.equal(musicTrack.sourceUrl, 'https://example.com/post');
    assert.equal(musicTrack.extra.catalogue.pcloudFileId, 123);
    assert.equal('temporaryUrl' in musicTrack.extra.catalogue, false);
});
