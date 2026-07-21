const assert = require('node:assert/strict');
const test = require('node:test');
const { MessageFlags } = require('discord.js');
const event = require('../events/playlistBrowser');
const { PlaylistBrowser } = require('../src/playlists/PlaylistBrowser');

test('Now Playing playlist button captures a stable song and opens a private picker', async t => {
    const captured = { title: 'Captured song', url: 'https://youtube.example/stable', platform: 'youtube' };
    const browser = new PlaylistBrowser({ service: {
        captureTrack: track => ({ ...track, ...captured }),
        listPlaylists: () => [],
    } });

    let reply;
    await event.execute({
        customId: 'playlist:current:current-session',
        isButton: () => true,
        isStringSelectMenu: () => false,
        isModalSubmit: () => false,
        user: { id: 'user-1' },
        guild: { id: 'guild-1' },
        client: {
            playlistBrowser: browser,
            players: new Map([['guild-1', {
                sessionId: 'current-session',
                currentTrack: { title: 'Live player object', stream: 'temporary' },
            }]]),
        },
        reply: payload => { reply = payload; },
    });

    assert.equal(reply.flags, MessageFlags.Ephemeral);
    assert.match(reply.embeds[0].toJSON().title, /Add Current Song/);
    const session = [...browser.sessions.values()].find(item => item.ownerId === 'user-1');
    assert.equal(session.pendingTrack.url, captured.url);
    browser.deleteSession(session.id);
});

test('rejects a playlist button from a stale Now Playing card', async () => {
    let reply;
    await event.execute({
        customId: 'playlist:current:old-session',
        isButton: () => true,
        isStringSelectMenu: () => false,
        isModalSubmit: () => false,
        user: { id: 'user-1' },
        guild: { id: 'guild-1' },
        client: { players: new Map([['guild-1', {
            sessionId: 'new-session',
            currentTrack: { title: 'Song' },
        }]]), playlistBrowser: new PlaylistBrowser({ service: {} }) },
        replied: false,
        deferred: false,
        reply: payload => { reply = payload; },
    });
    assert.equal(reply.flags, MessageFlags.Ephemeral);
    assert.match(reply.content, /no longer current/);
});
