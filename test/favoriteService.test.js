const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');
const DaisyStateStore = require('../src/state/DaisyStateStore');
const { FavoriteService } = require('../src/favorites/FavoriteService');

function createFixture(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'daisy-favorites-'));
    const store = new DaisyStateStore(path.join(directory, 'state.db'));
    const catalogueTrack = {
        id: 42,
        stableKey: 'phyu:track:42',
        title: 'Catalogue song',
        artist: 'Catalogue artist',
        album: 'Catalogue album',
        postUrl: 'https://example.com/post',
        pcloudCode: 'stable-code',
        pcloudFileId: 123,
    };
    const catalogue = { getTrackById: id => Number(id) === 42 ? catalogueTrack : null };
    const service = new FavoriteService({ store, catalogue });
    t.after(() => {
        store.close();
        fs.rmSync(directory, { recursive: true, force: true });
    });
    return { store, service };
}

test('migrates a constrained favorites schema with an indexed user listing', t => {
    const { store } = createFixture(t);
    const versions = store.database.prepare('SELECT version FROM state_schema_migrations').all();
    const plan = store.database.prepare(`
        EXPLAIN QUERY PLAN
        SELECT * FROM favorites
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 25
    `).get('user-1');

    assert.deepEqual(versions.map(row => row.version), [1]);
    assert.match(plan.detail, /idx_favorites_user_created/);
    assert.throws(() => store.upsertFavorite({
        userId: 'user-1',
        sourceType: 'temporary-stream',
        sourceKey: 'x',
        title: 'x',
        playbackJson: '{}',
    }), /Unsupported favorite source/);
});

test('adds, refreshes, lists, isolates, and removes user favorites', t => {
    const { service } = createFixture(t);
    const track = {
        id: 'video-1',
        title: 'YouTube song',
        artist: 'Singer',
        album: 'Single',
        url: 'https://www.youtube.com/watch?v=video-1',
        duration: 180,
        thumbnail: 'https://example.com/thumb.jpg',
        platform: 'youtube',
        stream: 'https://temporary.example/audio',
    };

    const first = service.addFavorite('user-1', track);
    const duplicate = service.addFavorite('user-1', { ...track, title: 'Updated title' });
    service.addFavorite('user-2', track);

    assert.equal(first.created, true);
    assert.equal(duplicate.created, false);
    assert.equal(service.countFavorites('user-1'), 1);
    assert.equal(service.countFavorites('user-2'), 1);
    assert.equal(service.listFavorites('user-1')[0].title, 'Updated title');
    assert.equal(service.searchFavorites('user-1', 'Updated')[0].title, 'Updated title');
    assert.doesNotMatch(service.listFavorites('user-1')[0].playbackJson, /temporary\.example/);
    assert.equal(
        service.removeFavoriteById('user-1', service.listFavorites('user-1')[0].id),
        true
    );
    assert.equal(service.countFavorites('user-1'), 0);
    assert.equal(service.countFavorites('user-2'), 1);
});

test('toggles a user favorite on and off using stable track identity', t => {
    const { service } = createFixture(t);
    const track = {
        id: 'video-toggle',
        title: 'Toggle song',
        url: 'https://www.youtube.com/watch?v=video-toggle',
        platform: 'youtube',
    };

    assert.equal(service.toggleFavorite('user-1', track).favorited, true);
    assert.equal(service.isFavorite('user-1', track), true);
    assert.equal(service.toggleFavorite('user-1', track).favorited, false);
    assert.equal(service.isFavorite('user-1', track), false);
});

test('re-resolves a Phyu favorite from its stable catalogue identity', async t => {
    const { service } = createFixture(t);
    service.addFavorite('user-1', {
        id: 'phyu:track:42',
        title: 'Saved title',
        artist: 'Saved artist',
        album: 'Saved album',
        url: 'phyu:track:42',
        sourceUrl: 'https://example.com/post',
        duration: 0,
        platform: 'phyu',
        extra: { catalogue: { trackId: 42, pcloudCode: 'stable-code', pcloudFileId: 123 } },
    });

    const favorite = service.listFavorites('user-1')[0];
    const resolved = await service.resolveFavorite(favorite);

    assert.equal(resolved.url, 'phyu:track:42');
    assert.equal(resolved.title, 'Catalogue song');
    assert.equal(resolved.extra.catalogue.pcloudFileId, 123);
});
