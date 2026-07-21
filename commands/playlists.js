const { MessageFlags, SlashCommandBuilder } = require('discord.js');
const { getPlaylistBrowser } = require('../src/playlists/PlaylistBrowser');

module.exports = {
    data: new SlashCommandBuilder()
        .setName('playlists')
        .setDescription('Open your personal and shared server playlists')
        .setDMPermission(false),

    async execute(interaction) {
        const browser = interaction.client?.playlistBrowser || getPlaylistBrowser();
        const session = browser.createSession(interaction.user.id, interaction.guild.id);
        return interaction.reply({
            ...browser.renderHome(session.id),
            flags: MessageFlags.Ephemeral,
        });
    },
};
