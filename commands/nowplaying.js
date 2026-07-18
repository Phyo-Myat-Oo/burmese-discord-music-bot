const { SlashCommandBuilder, EmbedBuilder, MessageFlags } = require('discord.js');
const LanguageManager = require('../src/LanguageManager');
const MusicEmbedManager = require('../src/MusicEmbedManager');

module.exports = {
    data: new SlashCommandBuilder()
        .setName('nowplaying')
        .setDescription('Shows information about currently playing song'),

    async execute(interaction, client) {
        try {
            const guild = interaction.guild;
            const guildId = guild.id;

            // Get music player
            const player = client.players.get(guild.id);
            if (!player) {
                const noPlayerMsg = await LanguageManager.getTranslation(guildId, 'commands.nowplaying.no_player');
                return await interaction.reply({
                    embeds: [await this.createErrorEmbed(noPlayerMsg, guildId)],
                    flags: MessageFlags.Ephemeral
                });
            }

            if (!player.currentTrack) {
                const noTrackMsg = await LanguageManager.getTranslation(guildId, 'commands.nowplaying.no_track');
                return await interaction.reply({
                    embeds: [await this.createErrorEmbed(noTrackMsg, guildId)],
                    flags: MessageFlags.Ephemeral
                });
            }

            if (!client.musicEmbedManager) client.musicEmbedManager = new MusicEmbedManager(client);
            await client.musicEmbedManager.showNowPlayingCard(player, interaction);

        } catch (error) {
            const guildId = interaction.guild.id;
            const errorMsg = await LanguageManager.getTranslation(guildId, 'commands.nowplaying.error_getting_info');
            await interaction.reply({
                embeds: [await this.createErrorEmbed(errorMsg, guildId)],
                flags: MessageFlags.Ephemeral
            });
        }
    },

    async createErrorEmbed(message, guildId) {
        const errorTitle = await LanguageManager.getTranslation(guildId, 'commands.nowplaying.error_title');
        return new EmbedBuilder()
            .setTitle(errorTitle)
            .setDescription(message)
            .setColor('#FF0000')
            .setTimestamp();
    },

    formatDuration(seconds) {
        if (!seconds || seconds === 0) return '0:00';

        // Ensure we work with integers to avoid floating point errors
        const totalSeconds = Math.floor(Number(seconds) || 0);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const remainingSeconds = totalSeconds % 60;

        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
        } else {
            return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
        }
    },

    formatTime(milliseconds) {
        const seconds = Math.floor(milliseconds / 1000);
        return this.formatDuration(seconds);
    },

    createProgressBar(current, total, length = 15) {
        if (!total || total === 0) return '▬'.repeat(length);

        const currentMs = typeof current === 'number' ? current : 0;
        const totalMs = total;
        const progress = Math.min(currentMs / totalMs, 1);
        const filledLength = Math.round(progress * length);

        const filled = '▬'.repeat(filledLength);
        const empty = '▬'.repeat(length - filledLength);
        const indicator = '🔘';

        if (filledLength === 0) {
            return indicator + empty;
        } else if (filledLength === length) {
            return filled + indicator;
        } else {
            return filled + indicator + empty.substring(1);
        }
    },

    getPlatformEmoji(platform) {
        const emojis = {
            youtube: '🔴',
            spotify: '🟢',
            soundcloud: '🟠',
            direct: '🔗'
        };
        return emojis[platform] || '🎵';
    }
};
