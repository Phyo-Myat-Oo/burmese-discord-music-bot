const crypto = require('crypto');
const {
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    EmbedBuilder,
    StringSelectMenuBuilder,
} = require('discord.js');
const config = require('../../config');
const PhyuCatalog = require('./PhyuCatalog');

const PAGE_SIZE = 25;
const SESSION_TTL_MS = 15 * 60 * 1000;

function truncate(value, maxLength) {
    const text = String(value || 'Unknown');
    if (text.length <= maxLength) return text;
    return `${text.slice(0, Math.max(0, maxLength - 1))}…`;
}

function pageNumber(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
}

class PhyuBrowser {
    constructor(options = {}) {
        this.catalogue = options.catalogue || new PhyuCatalog();
        this.sessionTtlMs = options.sessionTtlMs || SESSION_TTL_MS;
        this.sessions = new Map();
    }

    createSession(ownerId, artistQuery = '') {
        this.cleanupExpiredSessions();
        const id = crypto.randomBytes(6).toString('hex');
        const session = {
            id,
            ownerId: String(ownerId),
            artistQuery: String(artistQuery || '').trim(),
            artistPage: 0,
            selectedArtistId: null,
            albumPage: 0,
            expiresAt: Date.now() + this.sessionTtlMs,
        };
        this.sessions.set(id, session);
        return session;
    }

    createTrackSearchSession(ownerId, trackQuery) {
        const session = this.createSession(ownerId);
        session.trackQuery = String(trackQuery || '').trim();
        return session;
    }

    getSession(sessionId, ownerId) {
        this.cleanupExpiredSessions();
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('These catalogue suggestions have expired. Run /phyu play again.');
        if (String(ownerId) !== session.ownerId) {
            throw new Error('Only the person who opened this browser can use its controls.');
        }
        session.expiresAt = Date.now() + this.sessionTtlMs;
        return session;
    }

    cleanupExpiredSessions() {
        const now = Date.now();
        for (const [id, session] of this.sessions) {
            if (session.expiresAt <= now) this.sessions.delete(id);
        }
    }

    renderArtists(sessionId, requestedPage = 0) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Catalogue browser session not found.');
        const page = pageNumber(requestedPage);
        const offset = page * PAGE_SIZE;
        const finder = session.artistQuery
            ? this.catalogue.searchArtists.bind(this.catalogue)
            : this.catalogue.listArtists.bind(this.catalogue);
        const artists = session.artistQuery
            ? finder(session.artistQuery, { limit: PAGE_SIZE, offset })
            : finder({ limit: PAGE_SIZE, offset });
        const hasNext = session.artistQuery
            ? finder(session.artistQuery, { limit: 1, offset: offset + PAGE_SIZE }).length > 0
            : finder({ limit: 1, offset: offset + PAGE_SIZE }).length > 0;

        session.artistPage = page;
        const embed = new EmbedBuilder()
            .setTitle('🌼 Browse Burmese Artists')
            .setDescription(
                artists.length
                    ? `Choose an artist below${session.artistQuery ? ` • Filter: **${truncate(session.artistQuery, 100)}**` : ''}.`
                    : 'No artists matched this filter.'
            )
            .setColor(config.bot.embedColor)
            .setFooter({ text: `Artist page ${page + 1} • Browser expires after 15 minutes of inactivity` })
            .setTimestamp();

        if (!artists.length) return { embeds: [embed], components: [] };

        const select = new StringSelectMenuBuilder()
            .setCustomId(`phyu:artist-select:${session.id}:${page}`)
            .setPlaceholder('Select an artist')
            .addOptions(artists.map(artist => ({
                label: truncate(artist.name, 100),
                description: truncate(`${artist.albumCount} albums • ${artist.trackCount} tracks`, 100),
                value: String(artist.id),
            })));

        return {
            embeds: [embed],
            components: [
                new ActionRowBuilder().addComponents(select),
                this.paginationRow('artist-page', session.id, page, page > 0, hasNext),
            ],
        };
    }

    renderTrackSearch(sessionId, requestedPage = 0) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Catalogue browser session not found.');
        if (!session.trackQuery) throw new Error('A catalogue search query is required.');

        const page = pageNumber(requestedPage);
        const offset = page * PAGE_SIZE;
        const items = this.catalogue.searchItems(
            session.trackQuery,
            { limit: PAGE_SIZE, offset }
        );
        const hasNext = this.catalogue.searchItems(
            session.trackQuery,
            { limit: 1, offset: offset + PAGE_SIZE }
        ).length > 0;
        const description = items.map((item, index) =>
            item.type === 'album'
                ? `**${offset + index + 1}. 💿 Album** — ${truncate(item.title, 70)} (${item.trackCount} tracks)`
                : `**${offset + index + 1}. 🎵 Track** — ${truncate(item.title, 70)} — ${truncate(item.artist || 'Unknown artist', 40)}`
        ).join('\n');

        const embed = new EmbedBuilder()
            .setTitle('🌼 Phyu Catalogue Search')
            .setDescription(
                description || `No tracks or albums found for **${truncate(session.trackQuery, 100)}**.`
            )
            .setColor(config.bot.embedColor)
            .setFooter({ text: `Search: ${truncate(session.trackQuery, 80)} • Page ${page + 1}` })
            .setTimestamp();
        if (items[0]?.coverUrl) embed.setThumbnail(items[0].coverUrl);

        if (!items.length) return { embeds: [embed], components: [] };

        const select = new StringSelectMenuBuilder()
            .setCustomId(`phyu:search-item-select:${session.id}:${page}`)
            .setPlaceholder('Select a track or album')
            .addOptions(items.map(item => ({
                label: truncate(`[${item.type === 'album' ? 'Album' : 'Track'}] ${item.title}`, 100),
                description: truncate(
                    item.type === 'album'
                        ? `${item.artist || 'Various artists'} • ${item.trackCount} tracks`
                        : `${item.artist || 'Unknown artist'} • ${item.album || 'Unknown album'}`,
                    100
                ),
                value: `${item.type}:${item.id}`,
            })));

        return {
            embeds: [embed],
            components: [
                new ActionRowBuilder().addComponents(select),
                this.paginationRow('search-item-page', session.id, page, page > 0, hasNext),
            ],
        };
    }

    renderAlbums(sessionId, artistId, requestedPage = 0) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Catalogue browser session not found.');
        const artist = this.catalogue.getArtistById(artistId);
        if (!artist) throw new Error('That artist is no longer available in the catalogue.');

        const page = pageNumber(requestedPage);
        const offset = page * PAGE_SIZE;
        const albums = this.catalogue.getAlbumsByArtist(artist.id, { limit: PAGE_SIZE, offset });
        const hasNext = this.catalogue.getAlbumsByArtist(
            artist.id,
            { limit: 1, offset: offset + PAGE_SIZE }
        ).length > 0;

        session.selectedArtistId = artist.id;
        session.albumPage = page;
        const embed = new EmbedBuilder()
            .setTitle(`💿 ${truncate(artist.name, 240)}`)
            .setDescription(
                albums.length
                    ? `Choose an album • ${artist.albumCount} albums and ${artist.trackCount} tracks in the catalogue.`
                    : 'No albums are available for this artist.'
            )
            .setColor(config.bot.embedColor)
            .setFooter({ text: `Album page ${page + 1}` })
            .setTimestamp();

        if (!albums.length) {
            return {
                embeds: [embed],
                components: [this.backToArtistsRow(session.id)],
            };
        }
        if (albums[0].coverUrl) embed.setThumbnail(albums[0].coverUrl);

        const select = new StringSelectMenuBuilder()
            .setCustomId(`phyu:album-select:${session.id}:${artist.id}:${page}`)
            .setPlaceholder('Select an album')
            .addOptions(albums.map(album => ({
                label: truncate(album.title, 100),
                description: truncate(`${album.trackCount} track${album.trackCount === 1 ? '' : 's'}`, 100),
                value: String(album.id),
            })));

        return {
            embeds: [embed],
            components: [
                new ActionRowBuilder().addComponents(select),
                new ActionRowBuilder().addComponents(
                    new ButtonBuilder()
                        .setCustomId(`phyu:back-artists:${session.id}`)
                        .setLabel('Back to artists')
                        .setStyle(ButtonStyle.Secondary),
                    ...this.paginationButtons('album-page', session.id, page, page > 0, hasNext, artist.id)
                ),
            ],
        };
    }

    renderAlbum(sessionId, albumId, requestedPage = 0) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Catalogue browser session not found.');
        const album = this.catalogue.getAlbumById(albumId);
        if (!album) throw new Error('That album is no longer available in the catalogue.');

        const page = pageNumber(requestedPage);
        const offset = page * PAGE_SIZE;
        const tracks = this.catalogue.getAlbumTracksPage(album.id, { limit: PAGE_SIZE, offset });
        const pageCount = Math.max(1, Math.ceil(album.trackCount / PAGE_SIZE));
        const description = tracks.map((track, index) =>
            `**${offset + index + 1}.** ${truncate(track.title, 80)}${track.artist ? ` — ${truncate(track.artist, 50)}` : ''}`
        ).join('\n');

        const embed = new EmbedBuilder()
            .setTitle(`💿 ${truncate(album.title, 240)}`)
            .setDescription(description || 'No tracks are available for this album.')
            .setColor(config.bot.embedColor)
            .setFooter({ text: `${album.trackCount} tracks • Track page ${page + 1}/${pageCount}` })
            .setTimestamp();
        if (album.artist) embed.addFields({ name: 'Artist', value: truncate(album.artist, 1024) });
        if (album.coverUrl) embed.setThumbnail(album.coverUrl);

        const components = [];
        if (tracks.length) {
            components.push(new ActionRowBuilder().addComponents(
                new StringSelectMenuBuilder()
                    .setCustomId(`phyu:track-select:${session.id}:${album.id}:${page}`)
                    .setPlaceholder('Select one track to play')
                    .addOptions(tracks.map((track, index) => ({
                        label: truncate(`${offset + index + 1}. ${track.title}`, 100),
                        description: truncate(track.artist || album.artist || 'Unknown artist', 100),
                        value: String(track.id),
                    })))
            ));
        }

        components.push(new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`phyu:back-albums:${session.id}`)
                .setLabel('Back to albums')
                .setStyle(ButtonStyle.Secondary),
            new ButtonBuilder()
                .setCustomId(`phyu:play-album:${session.id}:${album.id}`)
                .setLabel(`Play full album (${album.trackCount})`)
                .setStyle(ButtonStyle.Primary)
        ));

        if (pageCount > 1) {
            components.push(this.paginationRow(
                'track-page', session.id, page, page > 0, page + 1 < pageCount, album.id
            ));
        }

        return { embeds: [embed], components };
    }

    paginationRow(action, sessionId, page, hasPrevious, hasNext, parentId = null) {
        return new ActionRowBuilder().addComponents(
            ...this.paginationButtons(action, sessionId, page, hasPrevious, hasNext, parentId)
        );
    }

    paginationButtons(action, sessionId, page, hasPrevious, hasNext, parentId = null) {
        const parent = parentId === null ? '' : `:${parentId}`;
        return [
            new ButtonBuilder()
                .setCustomId(`phyu:${action}:${sessionId}:${Math.max(0, page - 1)}${parent}`)
                .setLabel('Previous')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(!hasPrevious),
            new ButtonBuilder()
                .setCustomId(`phyu:${action}:${sessionId}:${page + 1}${parent}`)
                .setLabel('Next')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(!hasNext),
        ];
    }

    backToArtistsRow(sessionId) {
        return new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`phyu:back-artists:${sessionId}`)
                .setLabel('Back to artists')
                .setStyle(ButtonStyle.Secondary)
        );
    }
}

let sharedBrowser;
function getPhyuBrowser() {
    if (!sharedBrowser) sharedBrowser = new PhyuBrowser();
    return sharedBrowser;
}

module.exports = { PhyuBrowser, getPhyuBrowser, PAGE_SIZE };
