const assert = require('node:assert/strict');
const test = require('node:test');

const {
    cleanDisplayTitle,
    extractSourceLinks,
    matchMediaFireFiles,
    parseFeedEntry,
    parsePCloudJson,
    trackNumber,
} = require('../src/catalog/CatalogueUpdater');

test('extracts stable pCloud and MediaFire identifiers from a Blogspot post', () => {
    const links = extractSourceLinks(`
        <img src="https://images.example/cover.jpg">
        <a href="https://u.pcloud.link/publink/show?code=kZExample">pCloud</a>
        <a href="https://www.mediafire.com/folder/a4lt7vk2hu8ra/album">MediaFire folder</a>
        <a href="https://www.mediafire.com/file/r5pqdgm16w12xas/01.Song.mp3/file">MediaFire file</a>
    `);

    assert.deepEqual(links.pcloud, [{
        code: 'kZExample',
        url: 'https://u.pcloud.link/publink/show?code=kZExample',
    }]);
    assert.equal(links.mediafireFolders[0].key, 'a4lt7vk2hu8ra');
    assert.equal(links.mediafireFiles[0].quickkey, 'r5pqdgm16w12xas');
    assert.equal(links.mediafireFiles[0].filename, '01.Song.mp3');
    assert.equal(links.coverUrl, 'https://images.example/cover.jpg');
});

test('parses feed metadata and source links', () => {
    const entry = parseFeedEntry({
        title: { $t: 'Artist &amp; Album' },
        published: { $t: '2026-07-18T00:00:00Z' },
        updated: { $t: '2026-07-18T01:00:00Z' },
        link: [{ rel: 'alternate', href: 'https://phyuniwarpyar.blogspot.com/2026/example.html' }],
        content: { $t: '<a href="https://u.pcloud.link/publink/show?code=abc">play</a>' },
    });

    assert.equal(entry.title, 'Artist & Album');
    assert.equal(entry.links.pcloud[0].code, 'abc');
    assert.equal(entry.updated, '2026-07-18T01:00:00Z');
});

test('preserves pCloud file IDs larger than JavaScript safe integers', () => {
    const payload = parsePCloudJson('{"result":0,"metadata":{"fileid":725200524028587006}}');
    assert.equal(payload.metadata.fileid, '725200524028587006');
});

test('cleans track names and reads ASCII and Burmese track numbers', () => {
    assert.equal(cleanDisplayTitle('01. Example_Song.mp3'), 'Example Song');
    assert.equal(trackNumber('01. Example Song.mp3'), 1);
    assert.equal(trackNumber('၀၂။ Example Song.mp3'), null);
    assert.equal(trackNumber('၀၂-Example Song.mp3'), 2);
});

test('matches MediaFire files by normalized title, then unique track number', () => {
    const tracks = [
        { id: 1, title: '01. First_Song.mp3' },
        { id: 2, title: '02. A different spelling.mp3' },
    ];
    const files = [
        { quickkey: 'first', filename: '01. First Song.mp3' },
        { quickkey: 'second', filename: '02. Second Song.mp3' },
    ];
    const matches = matchMediaFireFiles(tracks, files);

    assert.equal(matches.length, 2);
    assert.equal(matches[0].file.quickkey, 'first');
    assert.equal(matches[1].file.quickkey, 'second');
});
