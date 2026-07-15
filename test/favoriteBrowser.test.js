const assert = require('node:assert/strict');
const test = require('node:test');
const { FavoriteBrowser } = require('../src/favorites/FavoriteBrowser');

function favorites(count) {
    return Array.from({ length: count }, (_, index) => ({
        id: index + 1,
        sourceType: index % 2 ? 'youtube' : 'phyu',
        title: `Favorite ${index + 1}`,
        artist: 'Artist',
        album: 'Album',
        thumbnail: null,
    }));
}

test('renders private favorites with select-menu pagination', () => {
    const records = favorites(30);
    const service = {
        countFavorites: () => records.length,
        listFavorites: (_userId, options) => records.slice(options.offset, options.offset + options.limit),
    };
    const browser = new FavoriteBrowser({ service });
    const session = browser.createSession('owner');
    const first = browser.renderPage(session.id, 0);
    const second = browser.renderPage(session.id, 1);
    const firstSelect = first.components[0].toJSON().components[0];
    const secondSelect = second.components[0].toJSON().components[0];

    assert.equal(firstSelect.options.length, 25);
    assert.equal(secondSelect.options.length, 5);
    assert.match(firstSelect.options[0].label, /^\[Phyu\]/);
    assert.equal(first.components[1].toJSON().components[1].disabled, false);
    assert.equal(second.components[1].toJSON().components[1].disabled, true);
    assert.throws(() => browser.getSession(session.id, 'intruder'), /Only the owner/);
});
