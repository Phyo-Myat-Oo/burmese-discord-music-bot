const assert = require('node:assert/strict');
const path = require('path');
const test = require('node:test');
const PhyuCatalog = require('../src/catalog/PhyuCatalog');
const { PhyuBrowser, PAGE_SIZE } = require('../src/catalog/PhyuBrowser');

const databasePath = path.resolve(__dirname, '..', 'data', 'daisy_v2', 'catalog.db');

test('renders artist, album, and track selectors from the real catalogue', t => {
    const catalogue = new PhyuCatalog(databasePath);
    const browser = new PhyuBrowser({ catalogue });
    t.after(() => catalogue.close());

    const session = browser.createSession('user-1', 'မေဆွိ');
    const artists = browser.renderArtists(session.id, 0);
    const artistSelect = artists.components[0].toJSON().components[0];

    assert.equal(artistSelect.type, 3);
    assert.ok(artistSelect.options.length > 0);
    assert.ok(artistSelect.options.length <= PAGE_SIZE);

    const artistId = Number.parseInt(artistSelect.options[0].value, 10);
    const albums = browser.renderAlbums(session.id, artistId, 0);
    const albumSelect = albums.components[0].toJSON().components[0];

    assert.equal(albumSelect.type, 3);
    assert.equal(albumSelect.options.length, PAGE_SIZE);
    assert.equal(albums.components[1].toJSON().components[2].disabled, false);

    const albumId = Number.parseInt(albumSelect.options[0].value, 10);
    const album = browser.renderAlbum(session.id, albumId, 0);
    const componentIds = album.components
        .flatMap(row => row.toJSON().components)
        .map(component => component.custom_id)
        .filter(Boolean);

    assert.ok(componentIds.some(id => id.startsWith('phyu:track-select:')));
    assert.ok(componentIds.some(id => id.startsWith('phyu:play-album:')));
    assert.ok(componentIds.some(id => id.startsWith('phyu:back-albums:')));
});

test('paginates a 96-track album within Discord select-menu limits', t => {
    const catalogue = new PhyuCatalog(databasePath);
    const browser = new PhyuBrowser({ catalogue });
    t.after(() => catalogue.close());

    const session = browser.createSession('user-2');
    const firstPage = browser.renderAlbum(session.id, 1584, 0);
    const lastPage = browser.renderAlbum(session.id, 1584, 3);
    const firstOptions = firstPage.components[0].toJSON().components[0].options;
    const lastOptions = lastPage.components[0].toJSON().components[0].options;

    assert.equal(firstOptions.length, 25);
    assert.equal(lastOptions.length, 21);
    assert.match(lastPage.embeds[0].data.footer.text, /4\/4/);
});

test('renders paginated track search results with a direct-play selector', t => {
    const catalogue = new PhyuCatalog(databasePath);
    const browser = new PhyuBrowser({ catalogue });
    t.after(() => catalogue.close());

    const session = browser.createTrackSearchSession('search-user', 'နွေ');
    const firstPage = browser.renderTrackSearch(session.id, 0);
    const secondPage = browser.renderTrackSearch(session.id, 1);
    const firstSelect = firstPage.components[0].toJSON().components[0];
    const secondSelect = secondPage.components[0].toJSON().components[0];

    assert.equal(firstSelect.options.length, PAGE_SIZE);
    assert.ok(secondSelect.options.length > 0);
    assert.match(firstSelect.custom_id, /^phyu:search-item-select:/);
    assert.ok(firstSelect.options.some(option => option.label.startsWith('[Track]')));
    assert.ok(firstSelect.options.some(option => option.label.startsWith('[Album]')));
    assert.ok(firstSelect.options.every(option => /^(track|album):\d+$/.test(option.value)));
    assert.equal(firstPage.components[1].toJSON().components[1].disabled, false);
});

test('restricts browser controls to their owner and expires inactive sessions', t => {
    const catalogue = new PhyuCatalog(databasePath);
    const browser = new PhyuBrowser({ catalogue, sessionTtlMs: 1000 });
    t.after(() => catalogue.close());

    const session = browser.createSession('owner');
    assert.throws(() => browser.getSession(session.id, 'someone-else'), /Only the person/);

    session.expiresAt = Date.now() - 1;
    assert.throws(() => browser.getSession(session.id, 'owner'), /expired/);
});
