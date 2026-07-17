const test = require('node:test');
const assert = require('node:assert/strict');

const searchCommand = require('../src/YouTubeSearchCommand');
const buttonHandler = require('../events/buttonHandler');

function searchResults(count = 20) {
    return Array.from({ length: count }, (_, index) => ({
        title: `Result ${index + 1}`,
        artist: 'Daisy Artist',
        duration: 180 + index,
        url: `https://youtube.com/watch?v=result-${index}`,
    }));
}

test('renders nine YouTube results per page with pagination controls', async () => {
    let payload;
    const interaction = {
        user: { id: 'listener' },
        editReply: async value => { payload = value; },
    };

    await searchCommand.showSearchMenu(interaction, searchResults(), 'daisy', 'guild');

    const menu = payload.components[0].components[0].toJSON();
    const controls = payload.components[1].components.map(component => component.toJSON());
    assert.equal(menu.custom_id, 'search_pick');
    assert.equal(menu.options.length, 9);
    assert.equal(menu.max_values, 9);
    assert.equal(controls[0].custom_id, 'search_page_previous');
    assert.equal(controls[0].disabled, true);
    assert.equal(controls[1].custom_id, 'search_page_next');
    assert.equal(controls[1].disabled, false);
    assert.equal(controls[2].custom_id, 'search_add');
    assert.equal(controls[2].disabled, true);
    searchCommand.deleteSession('listener');
});

test('retains selections while moving between YouTube result pages', async () => {
    const session = {
        query: 'daisy',
        results: searchResults(),
        page: 0,
        selectedIndexes: new Set(),
        timestamp: Date.now(),
    };

    searchCommand.updatePageSelection(session, ['1', '8']);
    session.page = 1;
    searchCommand.updatePageSelection(session, ['9', '17']);
    session.page = 0;

    const payload = await searchCommand.renderSearchMenu(session, 'guild');
    const menu = payload.components[0].components[0].toJSON();
    const defaults = menu.options.filter(option => option.default).map(option => option.value);
    const addButton = payload.components[1].components[2].toJSON();

    assert.deepEqual([...session.selectedIndexes].sort((a, b) => a - b), [1, 8, 9, 17]);
    assert.deepEqual(defaults, ['1', '8']);
    assert.equal(addButton.label, 'Add Selected (4)');
    assert.equal(addButton.disabled, false);
});

test('queues selections from multiple pages in overall result order', async () => {
    const results = searchResults();
    global.searchResults = new Map([['listener', {
        query: 'daisy',
        results,
        page: 1,
        selectedIndexes: new Set([17, 1, 9]),
        timestamp: Date.now(),
    }]]);

    let queuedData;
    const player = {};
    const interaction = {
        customId: 'search_add',
        user: { id: 'listener' },
        member: { voice: { channel: { id: 'voice' } } },
        guild: { id: 'guild' },
        channel: { id: 'text' },
        isStringSelectMenu: () => false,
        deferUpdate: async () => {},
        editReply: async () => {},
        reply: async () => {},
    };
    const client = {
        players: new Map([['guild', player]]),
        musicEmbedManager: {
            handleMusicData: async (_guildId, trackData) => {
                queuedData = trackData;
                return { success: true };
            },
        },
    };

    await buttonHandler.handleSearchInteraction(interaction, client);

    assert.equal(queuedData.isPlaylist, true);
    assert.deepEqual(queuedData.tracks.map(track => track.title), [
        'Result 2',
        'Result 10',
        'Result 18',
    ]);
    assert.equal(global.searchResults.has('listener'), false);
});
