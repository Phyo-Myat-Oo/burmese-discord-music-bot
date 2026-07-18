const assert = require('node:assert/strict');
const test = require('node:test');
const buttonHandler = require('../events/buttonHandler');
const modalHandler = require('../events/modalHandler');
const MusicPlayer = require('../src/MusicPlayer');
const PhyuAutocomplete = require('../src/catalog/PhyuAutocomplete');

test('leaves autoplay genre selections to the autoplay interaction handler', async () => {
    let replies = 0;
    await buttonHandler.execute({
        customId: 'autoplay_genre:listener-1:session-1',
        isButton: () => false,
        isStringSelectMenu: () => true,
        reply: async () => { replies += 1; },
    });

    assert.equal(replies, 0);
});

test('enables the selected autoplay genre and refreshes now playing', async () => {
    const player = {
        autoplay: false,
        voiceChannel: { id: 'voice-1' },
    };
    let reply;
    let refreshed = false;
    const client = {
        players: new Map([['guild-1', player]]),
        musicEmbedManager: {
            updateNowPlayingEmbed: async receivedPlayer => {
                assert.equal(receivedPlayer, player);
                refreshed = true;
            },
        },
    };
    const interaction = {
        guild: { id: 'guild-1' },
        member: {
            id: 'listener-1',
            voice: { channel: { id: 'voice-1' } },
            toString: () => '<@listener-1>',
        },
        values: ['phyu_random'],
        reply: async payload => { reply = payload; },
    };

    await modalHandler.handleAutoplayGenre(interaction, client);

    assert.equal(player.autoplay, 'phyu_random');
    assert.equal(refreshed, true);
    assert.equal(reply.flags[0], 1 << 6);
    assert.match(reply.embeds[0].data.description, /Phyu Random Catalogue/);
});

test('offers Phyu random mode in the autoplay picker', async () => {
    let reply;
    await buttonHandler.handleAutoplay({
        guild: { id: 'guild-1' },
        client: {},
        reply: async payload => { reply = payload; },
    }, {
        autoplay: false,
        sessionId: 'session-1',
    }, 'listener-1');

    const options = reply.components[0].toJSON().components[0].options;
    assert.ok(options.some(option =>
        option.value === 'phyu_random' && option.label === 'Phyu Random Catalogue'
    ));
});

test('starts another indexed Phyu track when random catalogue autoplay is active', async t => {
    const originalClientFactory = PhyuAutocomplete.getPhyuCatalogClient;
    const originalGlobalClients = global.clients;
    t.after(() => {
        PhyuAutocomplete.getPhyuCatalogClient = originalClientFactory;
        global.clients = originalGlobalClients;
    });

    const candidates = [
        { id: 42, stableKey: 'phyu:track:42', title: 'Unavailable Phyu song' },
        { id: 43, stableKey: 'phyu:track:43', title: 'Playable Phyu song' },
    ];
    let candidateIndex = 0;
    PhyuAutocomplete.getPhyuCatalogClient = () => ({
        getRandomTrack: async () => ({
            ...candidates[Math.min(candidateIndex++, candidates.length - 1)],
            artist: 'Random artist',
            album: 'Random album',
            postUrl: 'https://example.test/post',
            coverUrl: null,
            pcloudCode: 'public-code',
            pcloudFileId: '725200524028587006',
            mediafireQuickKey: null,
            mediafireUrl: null,
        }),
    });

    let played = false;
    let refreshed = false;
    let preloadAttempts = 0;
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'phyu_random',
        currentTrack: { id: 'phyu:track:1' },
        queue: [],
        guild: { members: { me: { user: { id: 'bot-1' } } } },
        preloadTrack: async track => {
            preloadAttempts += 1;
            if (track.id === 'phyu:track:42') throw new Error('Source unavailable');
            return true;
        },
        play: async function play() {
            played = true;
            assert.equal(this.currentTrack.platform, 'phyu');
        },
    });
    global.clients = {
        musicEmbedManager: {
            updateNowPlayingEmbed: async receivedPlayer => {
                assert.equal(receivedPlayer, player);
                refreshed = true;
            },
        },
    };

    await player.handleAutoplay();

    assert.equal(played, true);
    assert.equal(refreshed, true);
    assert.equal(preloadAttempts, 2);
    assert.equal(player.currentTrack.id, 'phyu:track:43');
    assert.equal(player.currentTrack.extra.catalogue.pcloudFileId, '725200524028587006');
});
