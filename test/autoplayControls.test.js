const assert = require('node:assert/strict');
const test = require('node:test');
const buttonHandler = require('../events/buttonHandler');
const modalHandler = require('../events/modalHandler');
const MusicPlayer = require('../src/MusicPlayer');
const MusicEmbedManager = require('../src/MusicEmbedManager');
const PhyuAutocomplete = require('../src/catalog/PhyuAutocomplete');
const YouTube = require('../src/YouTube');

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
    let persistedReason = null;
    const player = {
        autoplay: false,
        voiceChannel: { id: 'voice-1' },
        scheduleStatePersist: reason => { persistedReason = reason; },
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
    assert.equal(persistedReason, 'autoplay-mode');
    assert.equal(refreshed, true);
    assert.equal(reply.flags[0], 1 << 6);
    assert.match(reply.embeds[0].data.description, /Phyu Random Catalogue/);
});

test('shows the active autoplay mode consistently in the card and button', async () => {
    const manager = new MusicEmbedManager({ players: new Map() });
    const player = {
        autoplay: 'rnb',
        guild: { id: 'guild-1' },
        sessionId: 'session-1',
        requesterId: 'listener-1',
        paused: false,
        shuffle: false,
        loop: false,
        queue: [],
        previousTracks: [],
        currentTrack: { title: 'Song' },
    };

    assert.equal(manager.getAutoplayText(player), 'Autoplay: R&B');
    const rows = await manager.createControlButtons(player);
    const autoplayButton = rows
        .flatMap(row => row.components)
        .find(button => button.data.custom_id.startsWith('music_autoplay:'));
    assert.equal(autoplayButton.data.label, 'Autoplay: R&B');
});

test('starts autoplay immediately when the player is already idle', async () => {
    let autoplayCalls = 0;
    const player = {
        autoplay: false,
        currentTrack: null,
        queue: [],
        voiceChannel: { id: 'voice-1' },
        handleAutoplay: async () => { autoplayCalls += 1; },
    };
    const interaction = {
        guild: { id: 'guild-1' },
        member: {
            voice: { channel: { id: 'voice-1' } },
            toString: () => '<@listener-1>',
        },
        values: ['pop'],
        reply: async () => {},
    };

    await modalHandler.handleAutoplayGenre(interaction, {
        players: new Map([['guild-1', player]]),
        musicEmbedManager: { updateNowPlayingEmbed: async () => {} },
    });

    assert.equal(player.autoplay, 'pop');
    assert.equal(autoplayCalls, 1);
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
            return { success: true, track: this.currentTrack };
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

test('accepts a YouTube autoplay result whose flat search omitted duration', async t => {
    const originalSearch = YouTube.search;
    t.after(() => { YouTube.search = originalSearch; });
    YouTube.search = async () => [{
        id: 'youtube-1',
        title: 'Official pop song',
        duration: 0,
        url: 'https://www.youtube.com/watch?v=youtube-1',
        platform: 'youtube',
    }];

    let selectedTrack = null;
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'pop',
        currentTrack: { id: 'finished-track' },
        guild: { id: 'guild-1' },
        startAutoplayTrack: async track => { selectedTrack = track; },
    });

    await player.handleAutoplay();

    assert.equal(selectedTrack?.id, 'youtube-1');
});

test('genre autoplay does not cycle through the same two recent YouTube songs', async t => {
    const originalSearch = YouTube.search;
    t.after(() => { YouTube.search = originalSearch; });

    const queries = [];
    const searchResults = ['youtube-1', 'youtube-2', 'youtube-3'].map(id => ({
        id,
        title: `Official pop song ${id}`,
        duration: 180,
        url: `https://www.youtube.com/watch?v=${id}`,
        platform: 'youtube',
    }));
    YouTube.search = async query => {
        queries.push(query);
        return searchResults;
    };

    const selectedIds = [];
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'pop',
        autoplayInProgress: false,
        autoplayRecentTrackIds: [],
        autoplayFailedTrackIds: [],
        autoplaySearchCursor: 0,
        currentTrack: null,
        guild: { id: 'guild-1' },
        startAutoplayTrack: async function startAutoplayTrack(track) {
            selectedIds.push(track.id);
            this.currentTrack = track;
        },
    });

    for (let index = 0; index < 3; index += 1) {
        const previousId = player.currentTrack?.id || null;
        player.currentTrack = null;
        assert.equal(await player.handleAutoplay({ excludeIdentity: previousId }), true);
    }

    assert.equal(new Set(selectedIds).size, 3);
    assert.equal(new Set(queries.slice(0, 3)).size, 3);
    assert.deepEqual(new Set(player.autoplayRecentTrackIds), new Set(selectedIds));
});

test('genre autoplay remembers a YouTube candidate that failed to start', async t => {
    const originalSearch = YouTube.search;
    t.after(() => { YouTube.search = originalSearch; });

    const failed = {
        id: 'youtube-broken',
        title: 'Broken official rock song',
        duration: 180,
        url: 'https://www.youtube.com/watch?v=youtube-broken',
        platform: 'youtube',
    };
    const playable = {
        id: 'youtube-playable',
        title: 'Playable official rock song',
        duration: 180,
        url: 'https://www.youtube.com/watch?v=youtube-playable',
        platform: 'youtube',
    };
    let includePlayable = false;
    YouTube.search = async () => includePlayable ? [failed, playable] : [failed];

    const selectedIds = [];
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'rock',
        autoplayInProgress: false,
        autoplayRecentTrackIds: [],
        autoplayFailedTrackIds: [],
        autoplaySearchCursor: 0,
        currentTrack: null,
        guild: { id: 'guild-1' },
        scheduleAutoplayRetry: () => true,
        startAutoplayTrack: async track => {
            if (track.id === failed.id) throw new Error('Video unavailable');
            selectedIds.push(track.id);
        },
    });

    assert.equal(await player.handleAutoplay(), false);
    assert.deepEqual(player.autoplayFailedTrackIds, [failed.id]);

    includePlayable = true;
    assert.equal(await player.handleAutoplay(), true);
    assert.deepEqual(selectedIds, [playable.id]);
});

test('genre autoplay keeps only the latest twenty recently played IDs', () => {
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplayRecentTrackIds: [],
    });

    for (let index = 1; index <= 25; index += 1) {
        player.rememberRecentAutoplayTrack(`youtube-${index}`);
    }

    assert.equal(player.autoplayRecentTrackIds.length, 20);
    assert.equal(player.autoplayRecentTrackIds[0], 'youtube-6');
    assert.equal(player.autoplayRecentTrackIds.at(-1), 'youtube-25');
});

test('returns to autoplay when the last queued source fails', async () => {
    let autoplayCalls = 0;
    const replacement = { id: 'phyu:track:replacement' };
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'phyu_random',
        autoplayRecoveryInProgress: false,
        currentTrack: { id: 'phyu:track:broken' },
        queue: [],
        handleAutoplay: async function handleAutoplay() {
            autoplayCalls += 1;
            this.currentTrack = replacement;
        },
    });

    await player.handleError(new Error('Source unavailable'));

    assert.equal(autoplayCalls, 1);
    assert.equal(player.currentTrack, replacement);
    assert.equal(player.autoplayRecoveryInProgress, false);
});

test('clears the finished song before generating every next autoplay song', async () => {
    const finished = { id: 'youtube-finished', url: 'https://youtube.test/finished', duration: 100 };
    let receivedOptions;
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'pop',
        autoplayInProgress: false,
        isTransitioning: false,
        trackTimer: null,
        currentTrack: finished,
        resource: { playbackDuration: 100_000 },
        currentTrackStartOffsetMs: 0,
        previousTracks: [],
        currentDownloadedFile: null,
        loop: false,
        queue: [],
        handleAutoplay: async function handleAutoplay(options) {
            receivedOptions = options;
            assert.equal(this.currentTrack, null);
            this.currentTrack = { id: 'youtube-next' };
            return true;
        },
    });

    await player.handleTrackEnd('idle');

    assert.equal(receivedOptions.excludeIdentity, finished.id);
    assert.equal(player.currentTrack.id, 'youtube-next');
    assert.equal(player.isTransitioning, false);
});

test('schedules a future retry when autoplay search temporarily fails', async t => {
    const originalSearch = YouTube.search;
    t.after(() => { YouTube.search = originalSearch; });
    YouTube.search = async () => { throw new Error('Temporary YouTube failure'); };

    let retryError;
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'pop',
        autoplayInProgress: false,
        currentTrack: null,
        guild: { id: 'guild-1' },
        scheduleAutoplayRetry: error => { retryError = error; },
    });

    const started = await player.handleAutoplay();

    assert.equal(started, false);
    assert.match(retryError.message, /Temporary YouTube failure/);
    assert.equal(player.currentTrack, null);
    assert.equal(player.autoplayInProgress, false);
});

test('autoplay retry uses increasing backoff and can be cancelled', t => {
    const player = Object.assign(Object.create(MusicPlayer.prototype), {
        autoplay: 'pop',
        autoplayRetryTimer: null,
        autoplayRetryAttempts: 0,
        currentTrack: null,
        queue: [],
    });
    t.after(() => player.clearAutoplayRetry());

    assert.equal(player.scheduleAutoplayRetry(new Error('temporary')), true);
    assert.equal(player.autoplayRetryAttempts, 1);
    assert.ok(player.autoplayRetryTimer);
    assert.equal(player.scheduleAutoplayRetry(), false);

    player.clearAutoplayRetry();
    assert.equal(player.autoplayRetryTimer, null);
    assert.equal(player.autoplayRetryAttempts, 0);
});
