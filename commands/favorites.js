const { MessageFlags, SlashCommandBuilder } = require('discord.js');
const { toMusicTrack, validatePlayback, queueMusicTracks } = require('../src/catalog/PhyuPlayback');
const { getFavoriteService } = require('../src/favorites/FavoriteService');
const { getFavoriteBrowser } = require('../src/favorites/FavoriteBrowser');

function truncate(value, maxLength) {
    const text = String(value || 'Unknown');
    return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

function suggestionLabel(prefix, title, details) {
    return truncate(`[${prefix}] ${title}${details ? ` — ${details}` : ''}`, 100);
}

module.exports = {
    data: new SlashCommandBuilder()
        .setName('favorites')
        .setDescription('Search, save, remove, and play your favorite tracks')
        .addSubcommand(subcommand =>
            subcommand
                .setName('add')
                .setDescription('Search for a Phyu song to add')
                .addStringOption(option =>
                    option
                        .setName('query')
                        .setDescription('Type a song name and choose a suggestion')
                        .setRequired(true)
                        .setMaxLength(100)
                        .setAutocomplete(true)
                )
        )
        .addSubcommand(subcommand =>
            subcommand
                .setName('remove')
                .setDescription('Choose one of your saved favorites to remove')
                .addStringOption(option =>
                    option
                        .setName('query')
                        .setDescription('Type a saved song name and choose a suggestion')
                        .setRequired(true)
                        .setMaxLength(100)
                        .setAutocomplete(true)
                )
        )
        .addSubcommand(subcommand =>
            subcommand
                .setName('play')
                .setDescription('Play one of your saved favorites')
                .addStringOption(option =>
                    option
                        .setName('query')
                        .setDescription('Type a saved song name and choose a suggestion')
                        .setRequired(true)
                        .setMaxLength(100)
                        .setAutocomplete(true)
                )
        )
        .addSubcommand(subcommand =>
            subcommand.setName('list').setDescription('List and play your saved favorites')
        ),

    async autocomplete(interaction) {
        const action = interaction.options.getSubcommand();
        const focused = interaction.options.getFocused().trim();
        if (!focused) return interaction.respond([]);

        const service = getFavoriteService();
        if (action === 'add') {
            const tracks = service.catalogue.searchTracks(focused, { limit: 25 });
            return interaction.respond(tracks.map(track => ({
                name: suggestionLabel('Phyu Track', track.title, [track.artist, track.album].filter(Boolean).join(' • ')),
                value: `phyu:track:${track.id}`,
            })));
        }

        if (action === 'remove' || action === 'play') {
            const favorites = service.searchFavorites(interaction.user.id, focused, { limit: 25 });
            return interaction.respond(favorites.map(favorite => ({
                name: suggestionLabel(
                    favorite.sourceType === 'phyu' ? 'Phyu' : favorite.sourceType,
                    favorite.title,
                    favorite.artist
                ),
                value: `favorite:${favorite.id}`,
            })));
        }

        return interaction.respond([]);
    },

    async execute(interaction, client) {
        if (!interaction.deferred && !interaction.replied) {
            await interaction.deferReply({ flags: MessageFlags.Ephemeral });
        }

        try {
            const service = getFavoriteService();
            const action = interaction.options.getSubcommand();

            if (action === 'list') {
                const browser = getFavoriteBrowser();
                const session = browser.createSession(interaction.user.id);
                return interaction.editReply(browser.renderPage(session.id, 0));
            }

            const selectedValue = interaction.options.getString('query', true).trim();
            if (action === 'add') {
                const trackId = /^phyu:track:(\d+)$/.exec(selectedValue)?.[1];
                if (!trackId) {
                    return interaction.editReply({
                        content: 'Start typing a song name, then choose one of Daisy’s suggestions.',
                    });
                }
                const catalogueTrack = service.catalogue.getTrackById(trackId);
                if (!catalogueTrack) {
                    return interaction.editReply({ content: 'That suggested Phyu track is no longer available.' });
                }
                const musicTrack = toMusicTrack(catalogueTrack);
                const result = service.addFavorite(interaction.user.id, musicTrack);
                return interaction.editReply({
                    content: result.created
                        ? `⭐ Added **${musicTrack.title}** to your favorites.`
                        : `⭐ **${musicTrack.title}** is already in your favorites.`,
                });
            }

            if (action === 'remove' || action === 'play') {
                const favoriteId = /^favorite:(\d+)$/.exec(selectedValue)?.[1];
                if (!favoriteId) {
                    return interaction.editReply({
                        content: 'Start typing a saved song name, then choose one of your favorites.',
                    });
                }
                const favorite = service.getFavorite(interaction.user.id, favoriteId);
                if (!favorite) {
                    return interaction.editReply({ content: 'That favorite is no longer available.' });
                }

                if (action === 'remove') {
                    service.removeFavoriteById(interaction.user.id, favorite.id);
                    return interaction.editReply({ content: `Removed **${favorite.title}** from your favorites.` });
                }

                const validationError = validatePlayback(interaction);
                if (validationError) return interaction.editReply({ content: validationError });
                const track = service.resolveFavorite(favorite);
                const result = await queueMusicTracks(
                    interaction,
                    client,
                    [track],
                    { responseInteraction: false }
                );
                if (!result.success) {
                    return interaction.editReply({ content: result.message || 'Daisy could not play that favorite.' });
                }
                return interaction.editReply({ content: `⭐ Sent **${favorite.title}** to Daisy's player.` });
            }

            return interaction.editReply({ content: 'Unknown favorites command.' });
        } catch (error) {
            console.error('[Favorites] Command failed:', error);
            return interaction.editReply({ content: error.message || 'Daisy could not update your favorites.' });
        }
    },
};
