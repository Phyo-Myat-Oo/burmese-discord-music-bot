const { Events, MessageFlags } = require('discord.js');
const { getPlaylistBrowser } = require('../src/playlists/PlaylistBrowser');
const { validatePlayback, queueMusicTracks } = require('../src/catalog/PhyuPlayback');

async function replyPrivate(interaction, content) {
    const payload = { content, flags: MessageFlags.Ephemeral };
    return interaction.deferred || interaction.replied
        ? interaction.followUp(payload)
        : interaction.reply(payload);
}

async function updateBrowser(interaction, payload) {
    await interaction.deferUpdate();
    return interaction.editReply(payload);
}

module.exports = {
    name: Events.InteractionCreate,

    async execute(interaction) {
        const supported = interaction.isButton()
            || interaction.isStringSelectMenu()
            || interaction.isModalSubmit();
        if (!supported || !interaction.customId.startsWith('playlist:')) return;

        const browser = interaction.client.playlistBrowser || getPlaylistBrowser();
        const [, action, sessionId, value] = interaction.customId.split(':');

        if (action === 'current') {
            const player = interaction.client.players.get(interaction.guild.id);
            if (!player?.currentTrack) return replyPrivate(interaction, 'Daisy is not currently playing a song.');
            if (player.sessionId && player.sessionId !== sessionId) {
                return replyPrivate(interaction, 'This Now Playing card is no longer current.');
            }
            try {
                const session = browser.createSession(interaction.user.id, interaction.guild.id, {
                    purpose: 'add-current',
                    pendingTrack: browser.service.captureTrack(player.currentTrack),
                });
                return interaction.reply({
                    ...browser.renderHome(session.id),
                    flags: MessageFlags.Ephemeral,
                });
            } catch (error) {
                return replyPrivate(interaction, error.message || 'This song cannot be saved to a playlist.');
            }
        }

        let session;
        try {
            session = browser.getSession(sessionId, interaction.user.id, interaction.guild.id);
        } catch (error) {
            return replyPrivate(interaction, error.message);
        }
        const context = { userId: interaction.user.id, guildId: interaction.guild.id };

        try {
            if (action === 'scope') {
                session.scopeType = value === 'server' ? 'server' : 'personal';
                session.playlistId = null;
                session.selectedTrackId = null;
                session.page = 0;
                return updateBrowser(interaction, browser.renderHome(session.id));
            }
            if (action === 'open') {
                session.playlistId = Number.parseInt(interaction.values[0], 10);
                session.selectedTrackId = null;
                session.page = 0;
                const playlist = browser.service.getAccessiblePlaylist(session.playlistId, context);
                if (session.purpose === 'add-current') {
                    browser.service.addCurrentTrack(session.playlistId, context, session.pendingTrack);
                    browser.deleteSession(session.id);
                    return updateBrowser(interaction, {
                        content: `Added **${session.pendingTrack.title}** to **${playlist.name}**.`,
                        embeds: [],
                        components: [],
                    });
                }
                return updateBrowser(interaction, browser.renderDetail(session.id));
            }
            if (action === 'create') return interaction.showModal(browser.createNameModal(session.id, 'create'));
            if (action === 'rename') return interaction.showModal(browser.createNameModal(session.id, 'rename'));

            if (action === 'modal-create') {
                const playlist = browser.service.createPlaylist(
                    session.scopeType,
                    context,
                    interaction.fields.getTextInputValue('playlist_name')
                );
                session.playlistId = playlist.id;
                session.page = 0;
                session.selectedTrackId = null;
                if (session.purpose === 'add-current') {
                    browser.service.addCurrentTrack(playlist.id, context, session.pendingTrack);
                    browser.deleteSession(session.id);
                    return interaction.reply({
                        content: `Created **${playlist.name}** and added **${session.pendingTrack.title}**.`,
                        flags: MessageFlags.Ephemeral,
                    });
                }
                return interaction.update({
                    content: `Created **${playlist.name}**.`,
                    ...browser.renderDetail(session.id),
                });
            }
            if (action === 'modal-rename') {
                const playlist = browser.service.renamePlaylist(
                    session.playlistId,
                    context,
                    interaction.fields.getTextInputValue('playlist_name')
                );
                return interaction.update({
                    content: `Renamed playlist to **${playlist.name}**.`,
                    ...browser.renderDetail(session.id),
                });
            }
            if (action === 'page') return updateBrowser(interaction, browser.renderDetail(session.id, value));
            if (action === 'track') {
                session.selectedTrackId = Number.parseInt(interaction.values[0], 10);
                return updateBrowser(interaction, browser.renderDetail(session.id));
            }
            if (action === 'move') {
                if (!session.selectedTrackId) throw new Error('Select a track first.');
                browser.service.moveTrack(session.playlistId, session.selectedTrackId, value, context);
                return updateBrowser(interaction, browser.renderDetail(session.id));
            }
            if (action === 'remove') {
                if (!session.selectedTrackId) throw new Error('Select a track first.');
                browser.service.removeTrack(session.playlistId, session.selectedTrackId, context);
                session.selectedTrackId = null;
                return updateBrowser(interaction, browser.renderDetail(session.id));
            }
            if (action === 'add-current') {
                const player = interaction.client.players.get(interaction.guild.id);
                browser.service.addCurrentTrack(session.playlistId, context, player?.currentTrack);
                return updateBrowser(interaction, browser.renderDetail(session.id));
            }
            if (action === 'delete') return updateBrowser(interaction, browser.renderDeleteConfirmation(session.id));
            if (action === 'cancel-delete') return updateBrowser(interaction, browser.renderDetail(session.id));
            if (action === 'confirm-delete') {
                const playlist = browser.service.getAccessiblePlaylist(session.playlistId, context);
                browser.service.deletePlaylist(session.playlistId, context);
                session.playlistId = null;
                session.selectedTrackId = null;
                session.page = 0;
                return updateBrowser(interaction, {
                    content: `Deleted **${playlist.name}**.`,
                    ...browser.renderHome(session.id),
                });
            }
            if (action === 'back') {
                session.playlistId = null;
                session.selectedTrackId = null;
                session.page = 0;
                return updateBrowser(interaction, browser.renderHome(session.id));
            }
            if (action === 'close') {
                browser.deleteSession(session.id);
                return updateBrowser(interaction, {
                    content: 'Playlist manager closed.',
                    embeds: [],
                    components: [],
                });
            }
            if (action === 'play') {
                const validationError = validatePlayback(interaction);
                if (validationError) return replyPrivate(interaction, validationError);
                await interaction.deferUpdate();
                const result = await browser.service.resolveForPlayback(session.playlistId, context, {
                    shuffle: value === 'shuffle',
                });
                if (!result.tracks.length) {
                    throw new Error(result.skipped.length
                        ? 'Every saved track is currently unavailable.'
                        : 'This playlist is empty.');
                }
                const queued = await queueMusicTracks(interaction, interaction.client, result.tracks, {
                    isPlaylist: true,
                    responseInteraction: false,
                });
                if (!queued.success) throw new Error(queued.message || 'Daisy could not queue this playlist.');
                const skipped = result.skipped.length
                    ? ` Skipped ${result.skipped.length} unavailable track${result.skipped.length === 1 ? '' : 's'}.`
                    : '';
                return interaction.followUp({
                    content: `${value === 'shuffle' ? 'Shuffled and queued' : 'Queued'} ${result.tracks.length} track${result.tracks.length === 1 ? '' : 's'} from **${result.playlist.name}**.${skipped}`,
                    flags: MessageFlags.Ephemeral,
                });
            }
            return replyPrivate(interaction, 'Unknown playlist action.');
        } catch (error) {
            console.error('[Playlists] Interaction failed:', error);
            return replyPrivate(interaction, error.message || 'Daisy could not update that playlist.');
        }
    },
};
