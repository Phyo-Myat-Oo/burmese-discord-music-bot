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

test('uses direct streaming only for fresh YouTube playback', () => {
    const player = Object.create(MusicPlayer.prototype);

    assert.equal(player.shouldUseDirectYouTubeStream({ platform: 'youtube' }, 0), true);
    assert.equal(player.shouldUseDirectYouTubeStream({ platform: 'youtube' }, 12_000), false);
    assert.equal(player.shouldUseDirectYouTubeStream({ platform: 'phyu' }, 0), false);
});
