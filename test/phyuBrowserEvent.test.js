const assert = require('node:assert/strict');
const test = require('node:test');
const event = require('../events/phyuBrowser');
const { getPhyuBrowser } = require('../src/catalog/PhyuBrowser');

test('queues every track when the full-album button is used', async () => {
    const browser = getPhyuBrowser();
    const session = browser.createSession('user-1');
    let queuedData;
    let responseInteraction;
    let deferred = false;
    const voiceChannel = {
        id: 'voice-1',
        permissionsFor: () => ({ has: () => true }),
    };
    const player = {};
    const client = {
        players: new Map([['guild-1', player]]),
        musicEmbedManager: {
            handleMusicData: async (_guildId, trackData, _member, interactionForResponse) => {
                queuedData = trackData;
                responseInteraction = interactionForResponse;
                return { success: true };
            },
        },
    };
    const interaction = {
        customId: `phyu:play-album:${session.id}:1`,
        user: { id: 'user-1' },
        member: { voice: { channel: voiceChannel } },
        guild: {
            id: 'guild-1',
            members: { me: { voice: { channel: null } } },
        },
        channel: { id: 'text-1' },
        client,
        isButton: () => true,
        isStringSelectMenu: () => false,
        deferUpdate: async () => { deferred = true; },
        editReply: async payload => payload,
    };

    await event.execute(interaction);

    const expectedCount = (await browser.catalogue.getAlbumById(1)).trackCount;
    assert.equal(deferred, true);
    assert.equal(queuedData.isPlaylist, true);
    assert.equal(queuedData.tracks.length, expectedCount);
    assert.equal(responseInteraction, null);
    assert.ok(queuedData.tracks.every(track => track.platform === 'phyu'));
    assert.ok(queuedData.tracks.every(track => track.url.startsWith('phyu:track:')));
});

test('plays a dropdown search result without reusing the browser message', async () => {
    const browser = getPhyuBrowser();
    const session = browser.createTrackSearchSession('user-2', 'နွေ');
    const searchPayload = await browser.renderTrackSearch(session.id, 0);
    const selection = searchPayload.components[0].toJSON().components[0].options
        .find(option => option.value.startsWith('track:')).value;
    let responseInteraction = 'not-called';
    let editedPayload;
    const voiceChannel = {
        id: 'voice-1',
        permissionsFor: () => ({ has: () => true }),
    };
    const client = {
        players: new Map([['guild-1', {}]]),
        musicEmbedManager: {
            handleMusicData: async (_guildId, _trackData, _member, interactionForResponse) => {
                responseInteraction = interactionForResponse;
                return { success: true };
            },
        },
    };
    const interaction = {
        customId: `phyu:search-item-select:${session.id}:0`,
        values: [selection],
        user: { id: 'user-2' },
        member: { voice: { channel: voiceChannel } },
        guild: { id: 'guild-1', members: { me: { voice: { channel: null } } } },
        channel: { id: 'text-1' },
        client,
        isButton: () => false,
        isStringSelectMenu: () => true,
        deferUpdate: async () => {},
        editReply: async payload => { editedPayload = payload; return payload; },
    };

    await event.execute(interaction);

    assert.equal(responseInteraction, null);
    assert.match(editedPayload.content, /Track sent to Daisy's player/);
    assert.deepEqual(editedPayload.components, []);
});
