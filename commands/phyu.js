const { SlashCommandBuilder } = require('discord.js');
const PhyuCatalog = require('../src/catalog/PhyuCatalog');
const PhyuAutocomplete = require('../src/catalog/PhyuAutocomplete');
const { getPhyuBrowser } = require('../src/catalog/PhyuBrowser');
const {
    toMusicTrack,
    validatePlayback,
    queueCatalogueTracks,
} = require('../src/catalog/PhyuPlayback');

let catalogue;
const autocompleteSearch = new PhyuAutocomplete();

function getCatalogue() {
    if (!catalogue) catalogue = new PhyuCatalog();
    return catalogue;
}

function autocompleteLabel(item) {
    const type = item.type === 'album' ? 'Album' : 'Track';
    const details = item.type === 'album'
        ? `${item.trackCount} tracks${item.artist ? ` • ${item.artist}` : ''}`
        : [item.artist, item.album].filter(Boolean).join(' • ');
    const label = `[${type}] ${item.title}${details ? ` — ${details}` : ''}`;
    return label.length <= 100 ? label : `${label.slice(0, 99)}…`;
}

async function executeSearch(interaction) {
    const query = interaction.options.getString('query', true).trim();
    const browser = getPhyuBrowser();
    const session = browser.createTrackSearchSession(interaction.user.id, query);
    return interaction.editReply(browser.renderTrackSearch(session.id, 0));
}

async function executePlay(interaction, client) {
    const selectedValue = interaction.options.getString('query', true).trim();
    const selected = /^phyu:(track|album):(\d+)$/.exec(selectedValue);
    if (!selected) {
        return interaction.editReply({
            content: 'Start typing a song name, then choose one of Daisy’s suggestions before submitting.',
        });
    }

    const [, itemType, itemId] = selected;
    const tracks = itemType === 'album'
        ? getCatalogue().getAlbumTracks(itemId)
        : [getCatalogue().getTrackById(itemId)].filter(Boolean);
    if (!tracks.length) {
        return interaction.editReply({ content: `That suggested ${itemType} is no longer available.` });
    }

    const validationError = validatePlayback(interaction);
    if (validationError) return interaction.editReply({ content: validationError });

    const result = await queueCatalogueTracks(
        interaction,
        client,
        tracks,
        { isPlaylist: itemType === 'album' }
    );
    if (!result.success) {
        return interaction.editReply({ content: result.message || 'Daisy could not play that catalogue track.' });
    }
    return result;
}

module.exports = {
    data: new SlashCommandBuilder()
        .setName('phyu')
        .setDescription('Find and play music from the Phyu Ni War Pyar catalogue')
        .addSubcommand(subcommand =>
            subcommand
                .setName('search')
                .setDescription('Search with a paginated result picker')
                .addStringOption(option =>
                    option
                        .setName('query')
                        .setDescription('Song title, artist, or album name')
                        .setRequired(true)
                        .setMaxLength(100)
                )
        )
        .addSubcommand(subcommand =>
            subcommand
                .setName('play')
                .setDescription('Quick play using suggestions while you type')
                .addStringOption(option =>
                    option
                        .setName('query')
                        .setDescription('Type a song name and choose a suggestion')
                        .setRequired(true)
                        .setMaxLength(100)
                        .setAutocomplete(true)
                )
        ),

    async autocomplete(interaction) {
        const focused = interaction.options.getFocused().trim();
        if (!focused) return interaction.respond([]);

        const items = await autocompleteSearch.search(focused, { limit: 25 });
        return interaction.respond(items.map(item => ({
            name: autocompleteLabel(item),
            value: `phyu:${item.type}:${item.id}`,
        })));
    },

    async execute(interaction, client) {
        if (!interaction.deferred && !interaction.replied) {
            try {
                await interaction.deferReply();
            } catch (error) {
                if (error?.code === 10062 || error?.code === 40060) {
                    const age = Date.now() - interaction.createdTimestamp;
                    console.warn(`[PhyuCatalog] Interaction expired before acknowledgement (${age}ms old).`);
                    return;
                }
                throw error;
            }
        }

        try {
            const subcommand = interaction.options.getSubcommand();
            if (subcommand === 'search') return await executeSearch(interaction);
            if (subcommand === 'play') return await executePlay(interaction, client);
            return await interaction.editReply({ content: 'Unknown Phyu catalogue command.' });
        } catch (error) {
            console.error('[PhyuCatalog] Discord command failed:', error);
            return interaction.editReply({
                content: 'Daisy could not complete that catalogue request. Check the bot logs for details.',
            });
        }
    },

    toMusicTrack,
};
