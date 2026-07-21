const crypto = require('node:crypto');
const {
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    EmbedBuilder,
    ModalBuilder,
    StringSelectMenuBuilder,
    TextInputBuilder,
    TextInputStyle,
} = require('discord.js');
const config = require('../../config');
const { getPlaylistService } = require('./PlaylistService');

const PAGE_SIZE = 10;
const SESSION_TTL_MS = 15 * 60 * 1000;

function truncate(value, maxLength) {
    const text = String(value || 'Unknown');
    return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

function sourceLabel(sourceType) {
    return {
        phyu: 'Phyu',
        youtube: 'YouTube',
        spotify: 'Spotify',
        soundcloud: 'SoundCloud',
        direct: 'Direct',
    }[sourceType] || sourceType;
}

class PlaylistBrowser {
    constructor(options = {}) {
        this.service = options.service || getPlaylistService();
        this.sessionTtlMs = options.sessionTtlMs || SESSION_TTL_MS;
        this.sessions = new Map();
    }

    createSession(ownerId, guildId, options = {}) {
        this.cleanup();
        const session = {
            id: crypto.randomBytes(6).toString('hex'),
            ownerId: String(ownerId),
            guildId: String(guildId),
            scopeType: options.scopeType === 'server' ? 'server' : 'personal',
            playlistId: null,
            selectedTrackId: null,
            page: 0,
            purpose: options.purpose === 'add-current' ? 'add-current' : 'manage',
            pendingTrack: options.pendingTrack || null,
            expiresAt: Date.now() + this.sessionTtlMs,
        };
        this.sessions.set(session.id, session);
        return session;
    }

    getSession(sessionId, ownerId, guildId) {
        this.cleanup();
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('This playlist browser expired. Run `/playlists` again.');
        if (session.ownerId !== String(ownerId) || session.guildId !== String(guildId)) {
            throw new Error('Only the person who opened this playlist browser can use it.');
        }
        session.expiresAt = Date.now() + this.sessionTtlMs;
        return session;
    }

    deleteSession(sessionId) {
        return this.sessions.delete(sessionId);
    }

    cleanup() {
        const now = Date.now();
        for (const [id, session] of this.sessions) {
            if (session.expiresAt <= now) this.sessions.delete(id);
        }
    }

    context(session) {
        return { userId: session.ownerId, guildId: session.guildId };
    }

    renderHome(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Playlist session not found.');
        const playlists = this.service.listPlaylists(session.scopeType, this.context(session));
        const scopeLabel = session.scopeType === 'personal' ? 'Personal' : 'Server';
        const embed = new EmbedBuilder()
            .setTitle(session.purpose === 'add-current' ? '➕ Add Current Song to Playlist' : '🎶 Daisy Playlists')
            .setDescription([
                `**${scopeLabel} playlists**`,
                session.purpose === 'add-current'
                    ? 'Choose where to save the song captured from Now Playing.'
                    : 'Choose a playlist to open, or create a new one.',
                playlists.length
                    ? playlists.map((playlist, index) =>
                        `${index + 1}. **${truncate(playlist.name, 60)}** — ${playlist.trackCount} track${playlist.trackCount === 1 ? '' : 's'}`
                    ).join('\n')
                    : `No ${scopeLabel.toLowerCase()} playlists yet. Press **Create** to make one.`,
            ].join('\n\n'))
            .setColor(config.bot.embedColor)
            .setFooter({ text: `${playlists.length}/25 playlists • Private browser` })
            .setTimestamp();

        const scopeRow = new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`playlist:scope:${session.id}:personal`)
                .setLabel('Personal')
                .setEmoji('👤')
                .setStyle(session.scopeType === 'personal' ? ButtonStyle.Primary : ButtonStyle.Secondary),
            new ButtonBuilder()
                .setCustomId(`playlist:scope:${session.id}:server`)
                .setLabel('Server')
                .setEmoji('👥')
                .setStyle(session.scopeType === 'server' ? ButtonStyle.Primary : ButtonStyle.Secondary)
        );
        const rows = [scopeRow];

        if (playlists.length) {
            rows.push(new ActionRowBuilder().addComponents(
                new StringSelectMenuBuilder()
                    .setCustomId(`playlist:open:${session.id}`)
                    .setPlaceholder(session.purpose === 'add-current' ? 'Choose a playlist to save into' : 'Choose a playlist')
                    .addOptions(playlists.map(playlist => ({
                        label: truncate(playlist.name, 100),
                        description: truncate(`${playlist.trackCount}/50 tracks • ${scopeLabel}`, 100),
                        value: String(playlist.id),
                    })))
            ));
        }

        rows.push(new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`playlist:create:${session.id}`)
                .setLabel('Create')
                .setEmoji('➕')
                .setStyle(ButtonStyle.Success),
            new ButtonBuilder()
                .setCustomId(`playlist:close:${session.id}`)
                .setLabel('Close')
                .setStyle(ButtonStyle.Secondary)
        ));
        return { embeds: [embed], components: rows };
    }

    renderDetail(sessionId, requestedPage = null) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Playlist session not found.');
        const context = this.context(session);
        const playlist = this.service.getAccessiblePlaylist(session.playlistId, context);
        const total = playlist.trackCount;
        const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
        const requested = requestedPage === null ? session.page : Number.parseInt(requestedPage, 10) || 0;
        session.page = Math.max(0, Math.min(requested, pageCount - 1));
        const tracks = this.service.listTracks(playlist.id, context, {
            limit: PAGE_SIZE,
            offset: session.page * PAGE_SIZE,
        });
        if (!tracks.some(track => track.id === session.selectedTrackId)) session.selectedTrackId = null;

        const description = tracks.map(track =>
            `**${track.position}. [${sourceLabel(track.sourceType)}]** ${truncate(track.title, 70)}`
            + `${track.artist ? ` — ${truncate(track.artist, 35)}` : ''}`
        ).join('\n');
        const embed = new EmbedBuilder()
            .setTitle(`🎶 ${truncate(playlist.name, 200)}`)
            .setDescription(description || 'This playlist is empty. Play a song and press **Add Current**.')
            .setColor(config.bot.embedColor)
            .setFooter({
                text: `${playlist.scopeType === 'personal' ? 'Personal' : 'Server'} • ${total}/50 tracks • Page ${session.page + 1}/${pageCount}`,
            })
            .setTimestamp();
        if (tracks[0]?.thumbnail) embed.setThumbnail(tracks[0].thumbnail);

        const rows = [];
        if (tracks.length) {
            rows.push(new ActionRowBuilder().addComponents(
                new StringSelectMenuBuilder()
                    .setCustomId(`playlist:track:${session.id}`)
                    .setPlaceholder('Select a track to move or remove')
                    .addOptions(tracks.map(track => ({
                        label: truncate(`${track.position}. ${track.title}`, 100),
                        description: truncate(`[${sourceLabel(track.sourceType)}] ${track.artist || 'Saved track'}`, 100),
                        value: String(track.id),
                        default: track.id === session.selectedTrackId,
                    })))
            ));
        }

        rows.push(new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`playlist:page:${session.id}:${Math.max(0, session.page - 1)}`)
                .setLabel('Previous')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(session.page === 0),
            new ButtonBuilder()
                .setCustomId(`playlist:page:${session.id}:${session.page + 1}`)
                .setLabel('Next')
                .setStyle(ButtonStyle.Secondary)
                .setDisabled(session.page + 1 >= pageCount),
            new ButtonBuilder()
                .setCustomId(`playlist:back:${session.id}`)
                .setLabel('Back')
                .setStyle(ButtonStyle.Secondary)
        ));
        rows.push(new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`playlist:play:${session.id}:ordered`)
                .setLabel('Play All')
                .setEmoji('▶️')
                .setStyle(ButtonStyle.Success)
                .setDisabled(total === 0),
            new ButtonBuilder()
                .setCustomId(`playlist:play:${session.id}:shuffle`)
                .setLabel('Shuffle Play')
                .setEmoji('🔀')
                .setStyle(ButtonStyle.Primary)
                .setDisabled(total === 0),
            new ButtonBuilder()
                .setCustomId(`playlist:add-current:${session.id}`)
                .setLabel('Add Current')
                .setEmoji('➕')
                .setStyle(ButtonStyle.Primary)
        ));
        if (tracks.length) {
            rows.push(new ActionRowBuilder().addComponents(
                new ButtonBuilder()
                    .setCustomId(`playlist:move:${session.id}:up`)
                    .setLabel('Move Up')
                    .setStyle(ButtonStyle.Secondary)
                    .setDisabled(!session.selectedTrackId),
                new ButtonBuilder()
                    .setCustomId(`playlist:move:${session.id}:down`)
                    .setLabel('Move Down')
                    .setStyle(ButtonStyle.Secondary)
                    .setDisabled(!session.selectedTrackId),
                new ButtonBuilder()
                    .setCustomId(`playlist:remove:${session.id}`)
                    .setLabel('Remove')
                    .setEmoji('🗑️')
                    .setStyle(ButtonStyle.Danger)
                    .setDisabled(!session.selectedTrackId)
            ));
        }
        rows.push(new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`playlist:rename:${session.id}`)
                .setLabel('Rename')
                .setStyle(ButtonStyle.Secondary),
            new ButtonBuilder()
                .setCustomId(`playlist:delete:${session.id}`)
                .setLabel('Delete Playlist')
                .setStyle(ButtonStyle.Danger),
            new ButtonBuilder()
                .setCustomId(`playlist:close:${session.id}`)
                .setLabel('Close')
                .setStyle(ButtonStyle.Secondary)
        ));
        return { embeds: [embed], components: rows };
    }

    renderDeleteConfirmation(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Playlist session not found.');
        const playlist = this.service.getAccessiblePlaylist(session.playlistId, this.context(session));
        return {
            embeds: [new EmbedBuilder()
                .setTitle('Delete playlist?')
                .setDescription(`Delete **${playlist.name}** and all ${playlist.trackCount} saved tracks? This cannot be undone.`)
                .setColor('#ED4245')],
            components: [new ActionRowBuilder().addComponents(
                new ButtonBuilder()
                    .setCustomId(`playlist:confirm-delete:${session.id}`)
                    .setLabel('Yes, delete')
                    .setStyle(ButtonStyle.Danger),
                new ButtonBuilder()
                    .setCustomId(`playlist:cancel-delete:${session.id}`)
                    .setLabel('Cancel')
                    .setStyle(ButtonStyle.Secondary)
            )],
        };
    }

    createNameModal(sessionId, mode) {
        const session = this.sessions.get(sessionId);
        if (!session) throw new Error('Playlist session not found.');
        const rename = mode === 'rename';
        const input = new TextInputBuilder()
            .setCustomId('playlist_name')
            .setLabel('Playlist name')
            .setStyle(TextInputStyle.Short)
            .setMinLength(1)
            .setMaxLength(50)
            .setRequired(true);
        if (rename) {
            const playlist = this.service.getAccessiblePlaylist(session.playlistId, this.context(session));
            input.setValue(playlist.name);
        }
        return new ModalBuilder()
            .setCustomId(`playlist:modal-${rename ? 'rename' : 'create'}:${session.id}`)
            .setTitle(rename ? 'Rename Playlist' : `Create ${session.scopeType === 'personal' ? 'Personal' : 'Server'} Playlist`)
            .addComponents(new ActionRowBuilder().addComponents(input));
    }
}

let sharedBrowser;
function getPlaylistBrowser() {
    if (!sharedBrowser) sharedBrowser = new PlaylistBrowser();
    return sharedBrowser;
}

module.exports = { PAGE_SIZE, PlaylistBrowser, getPlaylistBrowser };
