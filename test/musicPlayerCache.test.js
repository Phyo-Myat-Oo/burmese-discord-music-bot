const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const MusicPlayer = require('../src/MusicPlayer');

test('preloads only the next five queued tracks in sequence', async () => {
    const player = Object.create(MusicPlayer.prototype);
    player.queue = Array.from({ length: 8 }, (_, index) => ({
        url: `track-${index + 1}`,
        title: `Track ${index + 1}`,
    }));
    player.preloadWindowPromise = null;
    player.preloadWindowPending = false;

    const preloaded = [];
    player.preloadTrack = async track => {
        preloaded.push(track.url);
    };

    await player.schedulePreloadWindow();

    assert.deepEqual(preloaded, [
        'track-1',
        'track-2',
        'track-3',
        'track-4',
        'track-5',
    ]);
});

test('preloads at most one Phyu track from the upcoming window', async () => {
    const player = Object.create(MusicPlayer.prototype);
    player.queue = Array.from({ length: 5 }, (_, index) => ({
        url: `phyu:track:${index + 1}`,
        title: `Phyu Track ${index + 1}`,
        platform: 'phyu',
    }));
    player.preloadWindowPromise = null;
    player.preloadWindowPending = false;

    const preloaded = [];
    player.preloadTrack = async track => {
        preloaded.push(track.url);
    };

    await player.schedulePreloadWindow();

    assert.deepEqual(preloaded, ['phyu:track:1']);
});

test('keeps non-Phyu preloading while limiting Phyu traffic', () => {
    const player = Object.create(MusicPlayer.prototype);
    player.queue = [
        { url: 'phyu:track:1', platform: 'phyu' },
        { url: 'youtube:1', platform: 'youtube' },
        { url: 'phyu:track:2', platform: 'phyu' },
        { url: 'youtube:2', platform: 'youtube' },
        { url: 'youtube:3', platform: 'youtube' },
    ];

    assert.deepEqual(
        player.getPreloadWindowTracks().map(track => track.url),
        ['phyu:track:1', 'youtube:1', 'youtube:2', 'youtube:3']
    );
});

test('sets only the final Discord Opus encoder to 96 kbps', () => {
    const player = Object.create(MusicPlayer.prototype);
    const configuredBitrates = [];
    const resource = {
        encoder: {
            setBitrate: bitrate => configuredBitrates.push(bitrate),
        },
    };

    assert.equal(player.configureDiscordOpusBitrate(resource), true);
    assert.deepEqual(configuredBitrates, [96_000]);
    assert.equal(player.configureDiscordOpusBitrate({}), false);
});

test('rolling cache removes the oldest files after ten tracks', async t => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'daisy-cache-test-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));

    const files = Array.from({ length: 12 }, (_, index) => {
        const filepath = path.join(directory, `track-${index + 1}.opus`);
        fs.writeFileSync(filepath, `track ${index + 1}`);
        return filepath;
    });

    const player = Object.create(MusicPlayer.prototype);
    player.queue = [];
    player.currentDownloadedFile = null;
    player.downloadingFiles = new Set();
    player.downloadedFiles = new Set(files);
    player.scheduleStatePersist = () => {};

    await player.enforceCacheLimit();

    assert.equal(player.downloadedFiles.size, 10);
    assert.equal(fs.existsSync(files[0]), false);
    assert.equal(fs.existsSync(files[1]), false);
    assert.equal(fs.existsSync(files[11]), true);
});
