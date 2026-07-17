const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const DaisyStateStore = require('../src/state/DaisyStateStore');

function createStore(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'daisy-greeting-'));
    const store = new DaisyStateStore(path.join(directory, 'state.db'));
    t.after(() => {
        store.close();
        fs.rmSync(directory, { recursive: true, force: true });
    });
    return store;
}

test('claims a greeting once per local date for each guild and user', t => {
    const store = createStore(t);

    assert.equal(store.claimDailyGreeting('guild-1', 'user-1', '2026-07-17'), true);
    assert.equal(store.claimDailyGreeting('guild-1', 'user-1', '2026-07-17'), false);
    assert.equal(store.claimDailyGreeting('guild-1', 'user-1', '2026-07-18'), true);
    assert.equal(store.claimDailyGreeting('guild-2', 'user-1', '2026-07-18'), true);
    assert.equal(store.claimDailyGreeting('guild-1', 'user-2', '2026-07-18'), true);
});

test('releases only the matching daily claim after a send failure', t => {
    const store = createStore(t);

    store.claimDailyGreeting('guild-1', 'user-1', '2026-07-17');
    assert.equal(store.releaseDailyGreeting('guild-1', 'user-1', '2026-07-16'), false);
    assert.equal(store.releaseDailyGreeting('guild-1', 'user-1', '2026-07-17'), true);
    assert.equal(store.claimDailyGreeting('guild-1', 'user-1', '2026-07-17'), true);
});

test('rejects malformed daily greeting identifiers and dates', t => {
    const store = createStore(t);

    assert.throws(() => store.claimDailyGreeting('', 'user-1', '2026-07-17'), /guildId/);
    assert.throws(() => store.claimDailyGreeting('guild-1', '', '2026-07-17'), /userId/);
    assert.throws(() => store.claimDailyGreeting('guild-1', 'user-1', '17-07-2026'), /localDate/);
});
