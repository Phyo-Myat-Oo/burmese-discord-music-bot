const crypto = require('crypto');
const {
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    EmbedBuilder,
    StringSelectMenuBuilder,
} = require('discord.js');
const config = require('../../config');
const { getFavoriteService } = require('./FavoriteService');

const PAGE_SIZE = 25;
const SESSION_TTL_MS = 15 * 60 * 1000;

function truncate(value, maxLength) {
    const text = String(value || 'Unknown');
    return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

function sourceLabel(sourceType) {
    const labels = {
        phyu: 'Phyu',
        youtube: 'YouTube',
        spotify: 'Spotify',
        soundcloud: 'SoundCloud',
        direct: 'Direct',
    };
    return labels[sourceType] || sourceType;
}

class FavoriteBrowser {
    constructor(options = {}) {
        this.service = options.service || getFavoriteService();
        this.sessionTtlMs = options.sessionTtlMs || SESSION_TTL_MS;
        this.sessions = new Map();
    }

    createSession(ownerId) {
        this.cleanup();
        const session = {
            id: crypto.randomBytes(6).toString('hex'),
            ownerId: String(ownerId),
            expiresAt: Date.now() + this.sessionTtlMs,
        };
        this.sessions.set(session.id, session);
        return session;
    }

    getSession(sessionId, ownerId) {
        this.cleanup();
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('This favorites list has expired. Run /favorites list again.');
        if (session.ownerId !== String(ownerId)) {
            throw new Error('Only the owner of this favorites list can use its controls.');
        }
        session.expiresAt = Date.now() + this.sessionTtlMs;
        return session;
    }

    cleanup() {
        const now = Date.now();
        for (const [id, session] of this.sessions) {
            if (session.expiresAt <= now) this.sessions.delete(id);
        }
    }

    renderPage(sessionId, requestedPage = 0) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Favorites session not found.');
        const page = Math.max(0, Number.parseInt(requestedPage, 10) || 0);
        const total = this.service.countFavorites(session.ownerId);
        const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
        const safePage = Math.min(page, pageCount - 1);
        const favorites = this.service.listFavorites(session.ownerId, {
            limit: PAGE_SIZE,
            offset: safePage * PAGE_SIZE,
        });

        const description = favorites.map((favorite, index) =>
            `**${safePage * PAGE_SIZE + index + 1}. [${sourceLabel(favorite.sourceType)}]** ` +
            `${truncate(favorite.title, 75)}${favorite.artist ? ` — ${truncate(favorite.artist, 45)}` : ''}`
        ).join('\n');

        const embed = new EmbedBuilder()
            .setTitle('⭐ Your Daisy Favorites')
            .setDescription(
                description ||
                'You have no favorites yet. Use `/favorites add` to search, or press Favorite on the now-playing card.'
            )
            .setColor(config.bot.embedColor)
            .setFooter({ text: `${total} favorite${total === 1 ? '' : 's'} • Page ${safePage + 1}/${pageCount}` })
            .setTimestamp();

        if (!favorites.length) return { embeds: [embed], components: [] };
        if (favorites[0].thumbnail) embed.setThumbnail(favorites[0].thumbnail);

        const select = new StringSelectMenuBuilder()
            .setCustomId(`favorite:select:${session.id}:${safePage}`)
            .setPlaceholder('Select a favorite to play')
            .addOptions(favorites.map(favorite => ({
                label: truncate(`[${sourceLabel(favorite.sourceType)}] ${favorite.title}`, 100),
                description: truncate(
                    [favorite.artist, favorite.album].filter(Boolean).join(' • ') || 'Saved track',
                    100
                ),
                value: String(favorite.id),
            })));

        const navigation = new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`favorite:page:${session.id}:${Math.max(0, safePage - 1)}`)
                .setLabel('Previous')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(safePage === 0),
            new ButtonBuilder()
                .setCustomId(`favorite:page:${session.id}:${safePage + 1}`)
                .setLabel('Next')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(safePage + 1 >= pageCount)
        );

        return {
            embeds: [embed],
            components: [new ActionRowBuilder().addComponents(select), navigation],
        };
    }
}

let sharedBrowser;
function getFavoriteBrowser() {
    if (!sharedBrowser) sharedBrowser = new FavoriteBrowser();
    return sharedBrowser;
}

module.exports = { FavoriteBrowser, getFavoriteBrowser, PAGE_SIZE };
