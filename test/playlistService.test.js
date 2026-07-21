const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { DatabaseSync } = require('node:sqlite');
const DaisyStateStore = require('../src/state/DaisyStateStore');
const {
    MAX_PLAYLISTS_PER_SCOPE,
    MAX_TRACKS_PER_PLAYLIST,
    PlaylistService,
} = require('../src/playlists/PlaylistService');

function youtubeTrack(number, overrides = {}) {
    return {
        id: `video-${number}`,
        title: `YouTube ${number}`,
        artist: 'Singer',
        url: `https://www.youtube.com/watch?v=video-${number}`,
        platform: 'youtube',
        duration: 180,
        ...overrides,
    };
}

function phyuTrack(id = 42, overrides = {}) {
    return {
        id: `phyu:track:${id}`,
        title: `Phyu ${id}`,
        artist: 'Myanmar artist',
        url: `phyu:track:${id}`,
        sourceUrl: 'https://example.com/post',
        platform: 'phyu',
        extra: { catalogue: { trackId: id } },
        ...overrides,
    };
}

function createFixture(t, options = {}) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'daisy-playlists-'));
    const databasePath = path.join(directory, 'state.db');
    const store = new DaisyStateStore(databasePath);
    const catalogue = options.catalogue || {
        getTrackById: id => Number(id) === 42 ? {
            id: 42,
            stableKey: 'phyu:track:42',
            title: 'Fresh catalogue title',
            artist: 'Fresh artist',
            postUrl: 'https://example.com/fresh-post',
            pcloudCode: 'fresh-code',
            pcloudFileId: 4242,
        } : null,
    };
    const service = new PlaylistService({ store, catalogue, random: options.random });
    t.after(() => {
        store.close();
        fs.rmSync(directory, { recursive: true, force: true });
    });
    return { directory, databasePath, service, store };
}

test('migration 3 preserves existing favorite and greeting state', t => {
    const fixture = createFixture(t);
    fixture.store.upsertFavorite({
        userId: 'user-1',
        sourceType: 'youtube',
        sourceKey: youtubeTrack(1).url,
        title: 'Existing favorite',
        playbackJson: JSON.stringify(youtubeTrack(1)),
    });
    fixture.store.claimDailyGreeting('guild-1', 'user-1', '2026-07-21');
    fixture.store.close();

    const old = new DatabaseSync(fixture.databasePath);
    old.exec('DROP TABLE playlist_tracks; DROP TABLE playlists; DELETE FROM state_schema_migrations WHERE version = 3;');
    old.close();

    fixture.store = new DaisyStateStore(fixture.databasePath);
    fixture.service.store = fixture.store;
    fixture.service.trackCodec.store = fixture.store;
    const versions = fixture.store.database.prepare('SELECT version FROM state_schema_migrations ORDER BY version').all();
    assert.deepEqual(versions.map(row => row.version), [1, 2, 3]);
    assert.equal(fixture.store.countFavorites('user-1'), 1);
    assert.equal(fixture.store.claimDailyGreeting('guild-1', 'user-1', '2026-07-21'), false);
    fixture.store.close();
});

test('isolates personal playlists by user and server playlists by guild', t => {
    const { service } = createFixture(t);
    const ownerA = { userId: 'user-a', guildId: 'guild-a' };
    const ownerAElsewhere = { userId: 'user-a', guildId: 'guild-b' };
    const userBSameGuild = { userId: 'user-b', guildId: 'guild-a' };
    const outsider = { userId: 'user-b', guildId: 'guild-b' };

    const personal = service.createPlaylist('personal', ownerA, 'My songs');
    const server = service.createPlaylist('server', ownerA, 'Party');

    assert.equal(service.getAccessiblePlaylist(personal.id, ownerAElsewhere).name, 'My songs');
    assert.equal(service.getAccessiblePlaylist(server.id, userBSameGuild).name, 'Party');
    assert.throws(() => service.getAccessiblePlaylist(personal.id, userBSameGuild), /cannot access/);
    assert.throws(() => service.getAccessiblePlaylist(server.id, outsider), /cannot access/);

    service.renamePlaylist(server.id, userBSameGuild, 'Shared party');
    assert.equal(service.getAccessiblePlaylist(server.id, ownerA).name, 'Shared party');
    assert.equal(service.deletePlaylist(server.id, userBSameGuild), true);
});

test('enforces normalized unique names and per-scope playlist limit', t => {
    const { service } = createFixture(t);
    const context = { userId: 'user-1', guildId: 'guild-1' };
    service.createPlaylist('personal', context, '  Road   Trip  ');
    assert.throws(() => service.createPlaylist('personal', context, 'road trip'), /already exists/);
    assert.throws(() => service.createPlaylist('personal', context, 'x'.repeat(51)), /between 1 and 50/);
    for (let index = 1; index < MAX_PLAYLISTS_PER_SCOPE; index += 1) {
        service.createPlaylist('personal', context, `List ${index}`);
    }
    assert.throws(() => service.createPlaylist('personal', context, 'One too many'), /maximum/);
    assert.doesNotThrow(() => service.createPlaylist('server', context, 'Road Trip'));
});

test('adds current YouTube and Phyu songs, prevents duplicates, and caps tracks', t => {
    const { service } = createFixture(t);
    const context = { userId: 'user-1', guildId: 'guild-1' };
    const playlist = service.createPlaylist('personal', context, 'Saved');

    service.addCurrentTrack(playlist.id, context, youtubeTrack(1, {
        stream: 'https://temporary.example/audio',
    }));
    service.addCurrentTrack(playlist.id, context, phyuTrack());
    assert.throws(() => service.addCurrentTrack(playlist.id, context, youtubeTrack(1)), /already in/);

    const saved = service.listTracks(playlist.id, context, { limit: 25 });
    assert.equal(saved[0].sourceKey, youtubeTrack(1).url);
    assert.doesNotMatch(saved[0].playbackJson, /temporary\.example/);
    assert.equal(saved[1].sourceKey, 'phyu:track:42');

    for (let index = 2; index < MAX_TRACKS_PER_PLAYLIST; index += 1) {
        service.addCurrentTrack(playlist.id, context, youtubeTrack(index));
    }
    assert.equal(service.getAccessiblePlaylist(playlist.id, context).trackCount, 50);
    assert.throws(() => service.addCurrentTrack(playlist.id, context, youtubeTrack(51)), /maximum/);
});

test('moves, removes, and compacts saved positions', t => {
    const { service } = createFixture(t);
    const context = { userId: 'user-1', guildId: 'guild-1' };
    const playlist = service.createPlaylist('personal', context, 'Ordered');
    [1, 2, 3].forEach(id => service.addCurrentTrack(playlist.id, context, youtubeTrack(id)));
    let tracks = service.listTracks(playlist.id, context, { limit: 25 });
    service.moveTrack(playlist.id, tracks[2].id, 'up', context);
    tracks = service.listTracks(playlist.id, context, { limit: 25 });
    assert.deepEqual(tracks.map(track => track.title), ['YouTube 1', 'YouTube 3', 'YouTube 2']);

    service.removeTrack(playlist.id, tracks[0].id, context);
    tracks = service.listTracks(playlist.id, context, { limit: 25 });
    assert.deepEqual(tracks.map(track => track.position), [1, 2]);
    assert.deepEqual(tracks.map(track => track.title), ['YouTube 3', 'YouTube 2']);
});

test('resolves fresh Phyu data, keeps stable URLs, skips broken entries, and shuffles a copy', async t => {
    const { service } = createFixture(t, { random: () => 0 });
    const context = { userId: 'user-1', guildId: 'guild-1' };
    const playlist = service.createPlaylist('personal', context, 'Playback');
    service.addCurrentTrack(playlist.id, context, youtubeTrack(1));
    service.addCurrentTrack(playlist.id, context, phyuTrack(42));
    service.addCurrentTrack(playlist.id, context, phyuTrack(99));

    const ordered = await service.resolveForPlayback(playlist.id, context);
    assert.deepEqual(ordered.tracks.map(track => track.title), ['YouTube 1', 'Fresh catalogue title']);
    assert.equal(ordered.tracks[0].url, youtubeTrack(1).url);
    assert.equal(ordered.tracks[1].extra.catalogue.pcloudFileId, 4242);
    assert.equal(ordered.skipped.length, 1);

    const shuffled = await service.resolveForPlayback(playlist.id, context, { shuffle: true });
    assert.deepEqual(shuffled.tracks.map(track => track.title), ['Fresh catalogue title', 'YouTube 1']);
    assert.deepEqual(
        service.listTracks(playlist.id, context, { limit: 25 }).map(track => track.title),
        ['YouTube 1', 'Phyu 42', 'Phyu 99']
    );
});
