const {
    EmbedBuilder,
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    PermissionFlagsBits,
    StringSelectMenuBuilder,
    StringSelectMenuOptionBuilder,
} = require('discord.js');
const config = require('../config.js');
const YouTube = require('./YouTube.js');
const LanguageManager = require('./LanguageManager');

const PAGE_SIZE = 9;
const SEARCH_LIMIT = 25;
const SESSION_TTL_MS = 5 * 60 * 1000;

module.exports = {
    PAGE_SIZE,
    SEARCH_LIMIT,

    async execute(interaction) {
        const query = interaction.options.getString('query');
        const guildId = interaction.guild.id;
        const member = interaction.member;
        const guild = interaction.guild;

        try {
            await interaction.deferReply();

            const validationResult = await this.validateRequest(interaction, member, guild);
            if (!validationResult.success) {
                return interaction.editReply({ content: validationResult.message });
            }

            // One flat yt-dlp request fetches enough entries for three pages.
            const results = await YouTube.search(query, SEARCH_LIMIT, guildId);
            if (!results || results.length === 0) {
                const noResultsMsg = await LanguageManager.getTranslation(guildId, 'commands.search.no_results');
                return interaction.editReply({ content: noResultsMsg });
            }

            return this.showSearchMenu(interaction, results, query, guildId);
        } catch (error) {
            console.error('[YouTubeSearch] Search failed:', error);
            const errorMsg = await LanguageManager.getTranslation(guildId, 'commands.search.error_search');
            return interaction.editReply({ content: errorMsg });
        }
    },

    async validateRequest(_interaction, member, guild) {
        if (!member.voice.channel) {
            const errorMsg = await LanguageManager.getTranslation(guild.id, 'commands.play.voice_channel_required');
            return { success: false, message: errorMsg };
        }

        const permissions = member.voice.channel.permissionsFor(guild.members.me);
        if (!permissions.has(PermissionFlagsBits.Connect) || !permissions.has(PermissionFlagsBits.Speak)) {
            const errorMsg = await LanguageManager.getTranslation(guild.id, 'commands.play.no_permissions');
            return { success: false, message: errorMsg };
        }

        const botVoiceChannel = guild.members.me.voice.channel;
        if (botVoiceChannel && botVoiceChannel.id !== member.voice.channel.id) {
            const errorMsg = await LanguageManager.getTranslation(guild.id, 'commands.play.same_channel_required');
            return { success: false, message: errorMsg };
        }

        return { success: true };
    },

    async showSearchMenu(interaction, results, query, guildId) {
        if (!global.searchResults) global.searchResults = new Map();

        const previousSession = global.searchResults.get(interaction.user.id);
        if (previousSession?.cleanupTimer) clearTimeout(previousSession.cleanupTimer);

        const session = {
            query,
            results,
            page: 0,
            selectedIndexes: new Set(),
            timestamp: Date.now(),
            cleanupTimer: null,
        };

        session.cleanupTimer = setTimeout(() => {
            if (global.searchResults?.get(interaction.user.id) === session) {
                global.searchResults.delete(interaction.user.id);
            }
        }, SESSION_TTL_MS);
        session.cleanupTimer.unref?.();
        global.searchResults.set(interaction.user.id, session);

        return interaction.editReply(await this.renderSearchMenu(session, guildId));
    },

    async renderSearchMenu(session, guildId) {
        const totalPages = Math.max(1, Math.ceil(session.results.length / PAGE_SIZE));
        session.page = Math.max(0, Math.min(Number(session.page) || 0, totalPages - 1));

        const start = session.page * PAGE_SIZE;
        const pageResults = session.results.slice(start, start + PAGE_SIZE);
        const [
            searchTitle,
            selectDescription,
            footerText,
            unknownTitle,
            unknownChannel,
            unknownDuration,
            cancelButtonLabel,
        ] = await Promise.all([
            LanguageManager.getTranslation(guildId, 'commands.search.title', { query: session.query }),
            LanguageManager.getTranslation(guildId, 'commands.search.select_description'),
            LanguageManager.getTranslation(guildId, 'commands.search.footer', { count: session.results.length }),
            LanguageManager.getTranslation(guildId, 'commands.search.unknown_title'),
            LanguageManager.getTranslation(guildId, 'commands.search.unknown_channel'),
            LanguageManager.getTranslation(guildId, 'commands.search.unknown_duration'),
            LanguageManager.getTranslation(guildId, 'commands.search.button_cancel'),
        ]);

        const selectedCount = session.selectedIndexes.size;
        const embed = new EmbedBuilder()
            .setTitle(searchTitle)
            .setColor(config.bot.embedColor)
            .setDescription([
                selectDescription,
                `စာမျက်နှာ **${session.page + 1}/${totalPages}** • ရွေးထားသောသီချင်း **${selectedCount} ပုဒ်**`,
                'စာမျက်နှာများပြောင်းပြီး သီချင်းရွေးနိုင်ပါတယ်။ ပြီးလျှင် **Add Selected** ကိုနှိပ်ပါ။',
            ].join('\n'))
            .setFooter({ text: `${footerText} • ${start + 1}-${start + pageResults.length}/${session.results.length}` })
            .setTimestamp();

        for (let pageIndex = 0; pageIndex < pageResults.length; pageIndex++) {
            const absoluteIndex = start + pageIndex;
            const result = pageResults[pageIndex];
            const title = result.title || unknownTitle;
            const uploader = result.artist || unknownChannel;
            const duration = this.formatDuration(result.duration, unknownDuration);
            const value = await LanguageManager.getTranslation(guildId, 'commands.search.result_line', {
                uploader,
                duration,
            });
            const selectedMark = session.selectedIndexes.has(absoluteIndex) ? ' ✅' : '';
            embed.addFields({
                name: `${absoluteIndex + 1}. ${title}${selectedMark}`,
                value,
                inline: false,
            });
        }

        const selectMenu = new StringSelectMenuBuilder()
            .setCustomId('search_pick')
            .setPlaceholder('ဒီစာမျက်နှာမှ သီချင်းများရွေးပါ')
            .setMinValues(0)
            .setMaxValues(pageResults.length);

        for (let pageIndex = 0; pageIndex < pageResults.length; pageIndex++) {
            const absoluteIndex = start + pageIndex;
            const result = pageResults[pageIndex];
            const title = result.title || unknownTitle;
            const artist = result.artist || unknownChannel;
            const duration = this.formatDuration(result.duration, unknownDuration);
            selectMenu.addOptions(
                new StringSelectMenuOptionBuilder()
                    .setLabel(this.truncate(`${absoluteIndex + 1}. ${title}`, 100))
                    .setDescription(this.truncate(`${artist} • ${duration}`, 100))
                    .setValue(String(absoluteIndex))
                    .setDefault(session.selectedIndexes.has(absoluteIndex))
            );
        }

        const selectRow = new ActionRowBuilder().addComponents(selectMenu);
        const controls = new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId('search_page_previous')
                .setLabel('Previous')
                .setEmoji('⬅️')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(session.page === 0),
            new ButtonBuilder()
                .setCustomId('search_page_next')
                .setLabel('Next')
                .setEmoji('➡️')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(session.page >= totalPages - 1),
            new ButtonBuilder()
                .setCustomId('search_add')
                .setLabel(`Add Selected (${selectedCount})`)
                .setEmoji('➕')
                .setStyle(ButtonStyle.Success)
                .setDisabled(selectedCount === 0),
            new ButtonBuilder()
                .setCustomId('search_cancel')
                .setLabel(cancelButtonLabel)
                .setEmoji('❌')
                .setStyle(ButtonStyle.Danger)
        );

        return { embeds: [embed], components: [selectRow, controls] };
    },

    updatePageSelection(session, values) {
        const start = session.page * PAGE_SIZE;
        const end = Math.min(start + PAGE_SIZE, session.results.length);
        for (let index = start; index < end; index++) {
            session.selectedIndexes.delete(index);
        }

        for (const value of values || []) {
            const index = Number(value);
            if (Number.isInteger(index) && index >= start && index < end) {
                session.selectedIndexes.add(index);
            }
        }
        session.timestamp = Date.now();
    },

    deleteSession(userId) {
        const session = global.searchResults?.get(userId);
        if (session?.cleanupTimer) clearTimeout(session.cleanupTimer);
        global.searchResults?.delete(userId);
    },

    truncate(value, maxLength) {
        const text = String(value || '');
        return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
    },

    formatDuration(seconds, unknownLabel = 'Unknown') {
        if (!seconds || seconds === 0) return unknownLabel;
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return hours > 0
            ? `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
            : `${minutes}:${secs.toString().padStart(2, '0')}`;
    },
};
