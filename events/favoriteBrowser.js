const { Events, MessageFlags } = require('discord.js');
const { getFavoriteBrowser } = require('../src/favorites/FavoriteBrowser');
const { validatePlayback, queueMusicTracks } = require('../src/catalog/PhyuPlayback');

async function replyPrivate(interaction, content) {
    if (interaction.deferred || interaction.replied) {
        return interaction.followUp({ content, flags: MessageFlags.Ephemeral });
    }
    return interaction.reply({ content, flags: MessageFlags.Ephemeral });
}

module.exports = {
    name: Events.InteractionCreate,

    async execute(interaction) {
        const supported = interaction.isButton() || interaction.isStringSelectMenu();
        if (!supported || !interaction.customId.startsWith('favorite:')) return;

        const browser = getFavoriteBrowser();
        const [, action, sessionId, value] = interaction.customId.split(':');

        if (action === 'current-toggle') {
            const player = interaction.client.players.get(interaction.guild.id);
            if (!player?.currentTrack) {
                return replyPrivate(interaction, 'Daisy is not currently playing a track.');
            }
            if (player.sessionId && player.sessionId !== sessionId) {
                return replyPrivate(interaction, 'This now-playing card is no longer current.');
            }

            try {
                const result = browser.service.toggleFavorite(interaction.user.id, player.currentTrack);
                return replyPrivate(
                    interaction,
                    result.favorited
                        ? `⭐ Added **${player.currentTrack.title}** to your favorites.`
                        : `Removed **${player.currentTrack.title}** from your favorites.`
                );
            } catch (error) {
                console.error('[Favorites] Current-track action failed:', error);
                return replyPrivate(interaction, error.message || 'Daisy could not update your favorites.');
            }
        }

        let session;
        try {
            session = browser.getSession(sessionId, interaction.user.id);
        } catch (error) {
            return replyPrivate(interaction, error.message);
        }

        try {
            if (action === 'page') {
                await interaction.deferUpdate();
                return interaction.editReply(browser.renderPage(session.id, value));
            }

            if (action !== 'select') return replyPrivate(interaction, 'Unknown favorites action.');
            const validationError = validatePlayback(interaction);
            if (validationError) return replyPrivate(interaction, validationError);

            const favorite = browser.service.getFavorite(session.ownerId, interaction.values[0]);
            if (!favorite) return replyPrivate(interaction, 'That favorite is no longer available.');

            await interaction.deferUpdate();
            const track = await browser.service.resolveFavorite(favorite);
            const result = await queueMusicTracks(
                interaction,
                interaction.client,
                [track],
                { responseInteraction: false }
            );
            if (!result.success) throw new Error(result.message || 'Daisy could not play that favorite.');

            await interaction.editReply({
                content: `⭐ Sent **${favorite.title}** to Daisy's player.`,
                embeds: [],
                components: [],
            });
            return result;
        } catch (error) {
            console.error('[Favorites] Browser interaction failed:', error);
            if (interaction.deferred || interaction.replied) {
                return interaction.editReply({ content: error.message, embeds: [], components: [] });
            }
            return replyPrivate(interaction, error.message || 'Daisy could not play that favorite.');
        }
    },
};
