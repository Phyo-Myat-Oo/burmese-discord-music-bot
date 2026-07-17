const assert = require('node:assert/strict');
const test = require('node:test');
const { MessageFlags } = require('discord.js');
const MusicEmbedManager = require('../src/MusicEmbedManager');

test('sends the personal greeting only to the configured Discord user', async () => {
    const messages = [];
    const claims = new Set();
    const stateStore = {
        claimDailyGreeting: (guildId, userId, localDate) => {
            const key = `${guildId}:${userId}:${localDate}`;
            if (claims.has(key)) return false;
            claims.add(key);
            return true;
        },
        releaseDailyGreeting: () => true,
    };
    const interaction = {
        guildId: 'guild-1',
        followUp: async payload => messages.push(payload),
    };
    const manager = new MusicEmbedManager(
        { players: new Map() },
        { stateStore, now: () => new Date('2026-07-17T12:00:00Z') }
    );

    const sent = await manager.sendSpecialGreeting(interaction, {
        id: '1003981930305441853',
    });
    const testAccountSent = await manager.sendSpecialGreeting(interaction, {
        id: '864008629401157684',
    });
    const ignored = await manager.sendSpecialGreeting(interaction, {
        id: '999999999999999999',
    });

    assert.equal(sent, true);
    assert.equal(testAccountSent, true);
    assert.equal(ignored, false);
    assert.equal(messages.length, 2);
    assert.equal(messages[0].flags, MessageFlags.Ephemeral);
    assert.match(messages[0].content, /ငါရဲ့ ဒေစီလေး/);
    assert.match(messages[0].content, /ဖြိုး။$/);
});

test('uses the Myanmar calendar date for the daily greeting boundary', () => {
    const manager = new MusicEmbedManager(
        { players: new Map() },
        { stateStore: {} }
    );

    assert.equal(
        manager.getGreetingLocalDate(new Date('2026-07-17T17:29:59Z')),
        '2026-07-17'
    );
    assert.equal(
        manager.getGreetingLocalDate(new Date('2026-07-17T17:30:00Z')),
        '2026-07-18'
    );
});
