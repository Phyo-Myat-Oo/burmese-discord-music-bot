const { Events, MessageFlags } = require('discord.js');
const { getPhyuBrowser } = require('../src/catalog/PhyuBrowser');
const { validatePlayback, queueCatalogueTracks } = require('../src/catalog/PhyuPlayback');

async function replyEphemeral(interaction, content) {
    if (interaction.deferred || interaction.replied) {
        return interaction.followUp({ content, flags: MessageFlags.Ephemeral });
    }
    return interaction.reply({ content, flags: MessageFlags.Ephemeral });
}

module.exports = {
    name: Events.InteractionCreate,

    async execute(interaction) {
        const isSupported = interaction.isButton() || interaction.isStringSelectMenu();
        if (!isSupported || !interaction.customId.startsWith('phyu:')) return;

        const browser = getPhyuBrowser();
        const [, action, sessionId, ...args] = interaction.customId.split(':');
        let session;

        try {
            session = browser.getSession(sessionId, interaction.user.id);
        } catch (error) {
            return replyEphemeral(interaction, error.message);
        }

        try {
            if (
                action === 'track-select' ||
                action === 'search-track-select' ||
                action === 'search-item-select' ||
                action === 'play-album' ||
                action === 'random-track' ||
                action === 'random-album'
            ) {
                const validationError = validatePlayback(interaction);
                if (validationError) return replyEphemeral(interaction, validationError);
            }

            await interaction.deferUpdate();

            switch (action) {
                case 'artist-select': {
                    const artistId = Number.parseInt(interaction.values[0], 10);
                    const page = Number.parseInt(args[0], 10);
                    session.artistPage = page;
                    return interaction.editReply(browser.renderAlbums(session.id, artistId, 0));
                }

                case 'artist-page':
                    return interaction.editReply(browser.renderArtists(session.id, args[0]));

                case 'search-track-page':
                case 'search-item-page':
                    return interaction.editReply(browser.renderTrackSearch(session.id, args[0]));

                case 'album-select': {
                    const artistId = Number.parseInt(args[0], 10);
                    const albumPage = Number.parseInt(args[1], 10);
                    const albumId = Number.parseInt(interaction.values[0], 10);
                    session.selectedArtistId = artistId;
                    session.albumPage = albumPage;
                    return interaction.editReply(browser.renderAlbum(session.id, albumId, 0));
                }

                case 'album-page': {
                    const page = Number.parseInt(args[0], 10);
                    const artistId = Number.parseInt(args[1], 10);
                    return interaction.editReply(browser.renderAlbums(session.id, artistId, page));
                }

                case 'back-artists':
                    return interaction.editReply(browser.renderArtists(session.id, session.artistPage));

                case 'track-page': {
                    const page = Number.parseInt(args[0], 10);
                    const albumId = Number.parseInt(args[1], 10);
                    return interaction.editReply(browser.renderAlbum(session.id, albumId, page));
                }

                case 'back-albums':
                    return interaction.editReply(
                        browser.renderAlbums(session.id, session.selectedArtistId, session.albumPage)
                    );

                case 'track-select':
                case 'search-track-select': {
                    const trackId = Number.parseInt(interaction.values[0], 10);
                    const track = browser.catalogue.getTrackById(trackId);
                    if (!track) throw new Error('That track is no longer available in the catalogue.');
                    const result = await queueCatalogueTracks(
                        interaction,
                        interaction.client,
                        [track],
                        { responseInteraction: false }
                    );
                    if (!result.success) {
                        return interaction.editReply({
                            content: result.message || 'Daisy could not play that track.',
                            embeds: [],
                            components: [],
                        });
                    }
                    await interaction.editReply({
                        content: `✅ **${track.title}** was sent to Daisy's player.`,
                        embeds: [],
                        components: [],
                    });
                    return result;
                }

                case 'search-item-select': {
                    const selected = /^(track|album):(\d+)$/.exec(interaction.values[0]);
                    if (!selected) throw new Error('That catalogue selection is invalid.');

                    const [, itemType, itemId] = selected;
                    const tracks = itemType === 'album'
                        ? browser.catalogue.getAlbumTracks(itemId)
                        : [browser.catalogue.getTrackById(itemId)].filter(Boolean);
                    if (!tracks.length) {
                        throw new Error(`That ${itemType} is no longer available in the catalogue.`);
                    }

                    const result = await queueCatalogueTracks(
                        interaction,
                        interaction.client,
                        tracks,
                        { isPlaylist: itemType === 'album', responseInteraction: false }
                    );
                    if (!result.success) {
                        return interaction.editReply({
                            content: result.message || `Daisy could not play that ${itemType}.`,
                            embeds: [],
                            components: [],
                        });
                    }

                    await interaction.editReply({
                        content: itemType === 'album'
                            ? `✅ Album queued: **${tracks[0].album}** (${tracks.length} tracks).`
                            : `✅ Track sent to Daisy's player: **${tracks[0].title}**.`,
                        embeds: [],
                        components: [],
                    });
                    return result;
                }

                case 'random-track': {
                    const track = browser.catalogue.getRandomTrack();
                    if (!track) throw new Error('No random track is available in the catalogue.');

                    const result = await queueCatalogueTracks(
                        interaction,
                        interaction.client,
                        [track],
                        { responseInteraction: false }
                    );
                    if (!result.success) {
                        return interaction.editReply({
                            content: result.message || 'Daisy could not play a random track.',
                            embeds: [],
                            components: [],
                        });
                    }
                    await interaction.editReply({
                        content: `âœ… Random track sent to Daisy's player: **${track.title}**.`,
                        embeds: [],
                        components: [],
                    });
                    return result;
                }

                case 'random-album': {
                    const album = browser.catalogue.getRandomAlbum();
                    if (!album) throw new Error('No random album is available in the catalogue.');

                    const tracks = browser.catalogue.getAlbumTracks(album.id);
                    if (!tracks.length) throw new Error('That random album has no playable tracks.');

                    const result = await queueCatalogueTracks(
                        interaction,
                        interaction.client,
                        tracks,
                        { isPlaylist: true, responseInteraction: false }
                    );
                    if (!result.success) {
                        return interaction.editReply({
                            content: result.message || 'Daisy could not queue a random album.',
                            embeds: [],
                            components: [],
                        });
                    }
                    await interaction.editReply({
                        content: `âœ… Random album queued: **${album.title}** (${tracks.length} tracks).`,
                        embeds: [],
                        components: [],
                    });
                    return result;
                }

                case 'play-album': {
                    const albumId = Number.parseInt(args[0], 10);
                    const tracks = browser.catalogue.getAlbumTracks(albumId);
                    const result = await queueCatalogueTracks(
                        interaction,
                        interaction.client,
                        tracks,
                        { isPlaylist: true, responseInteraction: false }
                    );
                    if (!result.success) {
                        return interaction.editReply({
                            content: result.message || 'Daisy could not queue that album.',
                            embeds: [],
                            components: [],
                        });
                    }
                    await interaction.editReply({
                        content: `✅ Full album queued: **${tracks[0]?.album || 'Catalogue album'}** (${tracks.length} tracks).`,
                        embeds: [],
                        components: [],
                    });
                    return result;
                }

                default:
                    return interaction.editReply({
                        content: 'Unknown catalogue browser action.',
                        embeds: [],
                        components: [],
                    });
            }
        } catch (error) {
            console.error('[PhyuBrowser] Interaction failed:', error);
            if (interaction.deferred || interaction.replied) {
                return interaction.editReply({
                    content: error.message || 'Daisy could not update the catalogue browser.',
                    embeds: [],
                    components: [],
                });
            }
            return replyEphemeral(interaction, error.message || 'Daisy could not update the catalogue browser.');
        }
    },
};
