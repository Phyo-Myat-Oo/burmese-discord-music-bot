const {
    EmbedBuilder,
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    StringSelectMenuBuilder,
    StringSelectMenuOptionBuilder
} = require('discord.js');
const config = require('../config');

const PAGE_SIZE = 20;

function clampPage(queueLength, page) {
    const lastPage = Math.max(0, Math.ceil(queueLength / PAGE_SIZE) - 1);
    return Math.min(Math.max(Number(page) || 0, 0), lastPage);
}

function truncate(value, maxLength) {
    const text = String(value || 'Unknown track');
    return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

function queueId(action, requesterId, sessionId, page = 0, selectedIndex = -1) {
    return `music_queue_${action}:${requesterId || 'unknown'}:${sessionId}:${page}:${selectedIndex}`;
}

function createQueueView(player, options = {}) {
    const queue = player.queue || [];
    const page = clampPage(queue.length, options.page);
    const requestedSelection = Number(options.selectedIndex);
    const selectedIndex = Number.isInteger(requestedSelection)
        && requestedSelection >= 0
        && requestedSelection < queue.length
        ? requestedSelection
        : -1;
    const requesterId = player.requesterId;
    const sessionId = player.sessionId;
    const start = page * PAGE_SIZE;
    const tracks = queue.slice(start, start + PAGE_SIZE);
    const totalPages = Math.max(1, Math.ceil(queue.length / PAGE_SIZE));

    const embed = new EmbedBuilder()
        .setTitle('📋 Queue')
        .setColor(config.bot.embedColor)
        .setTimestamp();

    if (player.currentTrack) {
        const sourceUrl = player.currentTrack.sourceUrl || player.currentTrack.url;
        embed.addFields({
            name: '🎵 Now Playing',
            value: `**[${player.currentTrack.title}](${sourceUrl})**`,
            inline: false
        });
    }

    if (tracks.length) {
        const list = tracks.map((track, index) => {
            const absoluteIndex = start + index;
            const marker = absoluteIndex === selectedIndex ? '➡️' : `\`${absoluteIndex + 1}.\``;
            return `${marker} **${truncate(track.title, 38)}**`;
        }).join('\n');

        embed.addFields({
            name: `Upcoming songs (${queue.length})`,
            value: list,
            inline: false
        });
        embed.setFooter({ text: `Page ${page + 1}/${totalPages} • Select a track, then select its new position.` });
    } else {
        embed.setDescription('There are no upcoming songs. The current song will keep playing.');
    }

    const components = [];

    if (tracks.length) {
        const trackSelect = new StringSelectMenuBuilder()
            .setCustomId(queueId('pick', requesterId, sessionId, page, selectedIndex))
            .setPlaceholder('Choose a song to reorder')
            .addOptions(tracks.map((track, index) => {
                const absoluteIndex = start + index;
                const option = new StringSelectMenuOptionBuilder()
                    .setLabel(`${absoluteIndex + 1}. ${truncate(track.title, 90)}`)
                    .setValue(String(absoluteIndex));
                if (absoluteIndex === selectedIndex) option.setDefault(true);
                return option;
            }));
        components.push(new ActionRowBuilder().addComponents(trackSelect));
    }

    if (selectedIndex >= 0 && queue.length > 1) {
        const destinationIndexes = [];
        const addDestination = index => {
            if (index >= 0 && index < queue.length && !destinationIndexes.includes(index)) {
                destinationIndexes.push(index);
            }
        };

        addDestination(0);
        for (let index = start; index < Math.min(start + PAGE_SIZE, queue.length); index += 1) {
            addDestination(index);
        }
        addDestination(queue.length - 1);

        const positionSelect = new StringSelectMenuBuilder()
            .setCustomId(queueId('move', requesterId, sessionId, page, selectedIndex))
            .setPlaceholder(`Move “${truncate(queue[selectedIndex].title, 60)}” to…`)
            .addOptions(destinationIndexes.map(index => {
                let suffix = '';
                if (index === 0) suffix = ' (First)';
                else if (index === queue.length - 1) suffix = ' (Last)';
                return new StringSelectMenuOptionBuilder()
                    .setLabel(`Position ${index + 1}${suffix}`)
                    .setValue(String(index));
            }));
        components.push(new ActionRowBuilder().addComponents(positionSelect));
    }

    const navigation = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
            .setCustomId(queueId('page', requesterId, sessionId, page - 1, selectedIndex))
            .setLabel('Previous')
            .setEmoji('◀️')
            .setStyle(ButtonStyle.Secondary)
            .setDisabled(page === 0),
        new ButtonBuilder()
            .setCustomId(queueId('page', requesterId, sessionId, page + 1, selectedIndex))
            .setLabel('Next')
            .setEmoji('▶️')
            .setStyle(ButtonStyle.Secondary)
            .setDisabled(page >= totalPages - 1),
        new ButtonBuilder()
            .setCustomId(queueId('clear', requesterId, sessionId, page, selectedIndex))
            .setLabel('Clear Queue')
            .setEmoji('🗑️')
            .setStyle(ButtonStyle.Danger)
            .setDisabled(queue.length === 0),
        new ButtonBuilder()
            .setCustomId(queueId('back', requesterId, sessionId, page, selectedIndex))
            .setLabel('Back')
            .setEmoji('⬅️')
            .setStyle(ButtonStyle.Primary)
    );
    components.push(navigation);

    return { embeds: [embed], components, page, selectedIndex };
}

module.exports = { PAGE_SIZE, clampPage, createQueueView };
