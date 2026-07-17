const assert = require('node:assert/strict');
const path = require('path');
const test = require('node:test');
const PhyuCatalog = require('../src/catalog/PhyuCatalog');

const databasePath = path.resolve(__dirname, '..', 'data', 'daisy_v2', 'catalog.db');

test('normalizes Burmese catalogue search text', () => {
    assert.equal(
        PhyuCatalog.normalizeSearchText(' ၀၁. မေ ရွက်ဝါ.MP3 '),
        '01မေရွက်ဝါmp3'
    );
});

test('opens schema v2 read-only and reports catalogue statistics', t => {
    const catalog = new PhyuCatalog(databasePath);
    t.after(() => catalog.close());

    assert.deepEqual(catalog.getStats(), {
        posts: 6866,
        albums: 5824,
        artists: 2049,
        tracks: 64370,
        failedAlbums: 2,
        mediafireTracks: 60051,
    });

    assert.throws(
        () => catalog.database.exec("INSERT INTO artists(name, search_key) VALUES ('x', 'x')"),
        /readonly|read-only/i
    );
});

test('loads a stable track with provider and source metadata', t => {
    const catalog = new PhyuCatalog(databasePath);
    t.after(() => catalog.close());

    const track = catalog.getTrackById(1);
    assert.equal(track.id, 1);
    assert.equal(track.stableKey, 'phyu:track:1');
    assert.ok(track.title);
    assert.ok(track.album);
    assert.ok(track.artist);
    assert.ok(track.pcloudCode);
    assert.ok(Number.isSafeInteger(track.pcloudFileId));
    assert.match(track.postUrl, /^https:\/\/phyuniwarpyar\.blogspot\.com\//);
    assert.match(track.coverUrl, /^https:\/\//);
    assert.equal('url' in track, false);
});

test('searches tracks, artists, and albums with normalized Burmese text', t => {
    const catalog = new PhyuCatalog(databasePath);
    t.after(() => catalog.close());

    const tracks = catalog.searchTracks(' မေ ရွက်ဝါ ', { limit: 5 });
    const artists = catalog.searchArtists('မေ ရွက်ဝါ', { limit: 5 });
    const albums = catalog.searchAlbums('မေတ္တာ ရေလှိုင်း', { limit: 5 });

    assert.equal(tracks.length, 5);
    assert.ok(tracks.every(track => track.stableKey.startsWith('phyu:track:')));
    assert.equal(artists[0].name, 'မေရွက်ဝါ');
    assert.ok(albums.some(album => album.title.includes('မေတ္တာရေလှိုင်း')));
});

test('supports artist browsing, album tracks, and random selections', t => {
    const catalog = new PhyuCatalog(databasePath);
    t.after(() => catalog.close());

    const artist = catalog.getArtistById(1);
    const albums = catalog.getAlbumsByArtist(1, { limit: 5 });
    const album = catalog.getAlbumById(1);
    const tracks = catalog.getAlbumTracks(1);
    const trackPage = catalog.getAlbumTracksPage(1, { limit: 2, offset: 1 });
    const randomTrack = catalog.getRandomTrack({ artistId: 1 });
    const randomAlbum = catalog.getRandomAlbum();

    assert.equal(artist.id, 1);
    assert.ok(artist.albumCount > 0);
    assert.ok(albums.length > 0);
    assert.equal(album.id, 1);
    assert.equal(tracks.length, album.trackCount);
    assert.deepEqual(trackPage, tracks.slice(1, 3));
    assert.ok(tracks.every(track => track.albumId === 1));
    assert.ok(randomTrack);
    assert.equal(randomTrack.artist.includes(artist.name), true);
    assert.ok(randomAlbum);
});

test('returns typed track and album results from one paginated search', t => {
    const catalog = new PhyuCatalog(databasePath);
    t.after(() => catalog.close());

    const items = catalog.searchItems('နွေ', { limit: 25 });

    assert.ok(items.some(item => item.type === 'track'));
    assert.ok(items.some(item => item.type === 'album'));
    assert.ok(items.every(item => item.stableKey === `phyu:${item.type}:${item.id}`));
});
