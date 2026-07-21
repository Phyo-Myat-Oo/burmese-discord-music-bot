const assert = require('node:assert/strict');
const test = require('node:test');
const { PAGE_SIZE, PlaylistBrowser } = require('../src/playlists/PlaylistBrowser');

function fakeService(playlistCount = 1, trackCount = 12) {
    const playlists = Array.from({ length: playlistCount }, (_, index) => ({
        id: index + 1,
        name: `Playlist ${index + 1}`,
        scopeType: 'personal',
        trackCount: index === 0 ? trackCount : 0,
    }));
    const tracks = Array.from({ length: trackCount }, (_, index) => ({
        id: index + 101,
        playlistId: 1,
        title: `Track ${index + 1}`,
        artist: 'Artist',
        sourceType: index % 2 ? 'phyu' : 'youtube',
        position: index + 1,
    }));
    return {
        listPlaylists: () => playlists,
        getAccessiblePlaylist: id => ({ ...playlists.find(item => item.id === Number(id)), trackCount }),
        listTracks: (_id, _context, options) => tracks.slice(options.offset, options.offset + options.limit),
    };
}

test('renders scope selection and no more than Discord component limits', () => {
    const browser = new PlaylistBrowser({ service: fakeService(25, 12) });
    const session = browser.createSession('user-1', 'guild-1');
    const home = browser.renderHome(session.id);
    assert.equal(home.components.length, 3);
    assert.equal(home.components[1].toJSON().components[0].options.length, 25);

    session.playlistId = 1;
    const detail = browser.renderDetail(session.id);
    assert.ok(detail.components.length <= 5);
    assert.equal(detail.components[0].toJSON().components[0].options.length, PAGE_SIZE);
    assert.ok(detail.components.every(row => row.toJSON().components.length <= 5));
});

test('paginates playlist tracks and keeps selection owner-bound', () => {
    const browser = new PlaylistBrowser({ service: fakeService(1, 12) });
    const session = browser.createSession('owner', 'guild-1');
    session.playlistId = 1;

    const page = browser.renderDetail(session.id, 1);
    assert.equal(session.page, 1);
    assert.equal(page.components[0].toJSON().components[0].options.length, 2);
    assert.throws(() => browser.getSession(session.id, 'intruder', 'guild-1'), /Only the person/);
    assert.throws(() => browser.getSession(session.id, 'owner', 'guild-2'), /Only the person/);
});

test('creates name modals and delete confirmation controls', () => {
    const browser = new PlaylistBrowser({ service: fakeService() });
    const session = browser.createSession('owner', 'guild-1');
    session.playlistId = 1;
    const create = browser.createNameModal(session.id, 'create').toJSON();
    const rename = browser.createNameModal(session.id, 'rename').toJSON();
    const confirmation = browser.renderDeleteConfirmation(session.id);

    assert.equal(create.custom_id, `playlist:modal-create:${session.id}`);
    assert.equal(rename.custom_id, `playlist:modal-rename:${session.id}`);
    assert.equal(create.components[0].components[0].max_length, 50);
    assert.deepEqual(
        confirmation.components[0].toJSON().components.map(component => component.custom_id.split(':')[1]),
        ['confirm-delete', 'cancel-delete']
    );
});

test('expires inactive playlist sessions', () => {
    const browser = new PlaylistBrowser({ service: fakeService(), sessionTtlMs: -1 });
    const session = browser.createSession('owner', 'guild-1');
    assert.throws(() => browser.getSession(session.id, 'owner', 'guild-1'), /expired/);
});
