const { PermissionFlagsBits } = require('discord.js');
const MusicPlayer = require('../MusicPlayer');
const MusicEmbedManager = require('../MusicEmbedManager');

function toMusicTrack(track) {
    return {
        id: track.stableKey,
        title: track.title,
        artist: track.artist || 'Unknown artist',
        album: track.album || null,
        url: track.stableKey,
        sourceUrl: track.postUrl || track.pcloudShareUrl,
        duration: track.duration || 0,
        thumbnail: track.coverUrl || null,
        platform: 'phyu',
        type: 'track',
        extra: {
            catalogue: {
                trackId: track.id,
                stableKey: track.stableKey,
                pcloudCode: track.pcloudCode,
                pcloudFileId: track.pcloudFileId,
                mediafireQuickKey: track.mediafireQuickKey || null,
                mediafireUrl: track.mediafireUrl || null,
            },
        },
    };
}

function validatePlayback(interaction) {
    const member = interaction.member;
    const guild = interaction.guild;

    if (!member?.voice?.channel) return 'Join a voice channel first.';

    const permissions = member.voice.channel.permissionsFor(guild.members.me);
    if (!permissions?.has(PermissionFlagsBits.Connect) || !permissions?.has(PermissionFlagsBits.Speak)) {
        return 'Daisy needs the Connect and Speak permissions in your voice channel.';
    }

    const botVoiceChannel = guild.members.me.voice.channel;
    if (botVoiceChannel && botVoiceChannel.id !== member.voice.channel.id) {
        return 'Join the same voice channel as Daisy first.';
    }

    return null;
}

async function queueMusicTracks(interaction, client, musicTracks, options = {}) {
    if (!Array.isArray(musicTracks) || musicTracks.length === 0) {
        return { success: false, message: 'No music tracks were provided.' };
    }
    const guild = interaction.guild;
    const member = interaction.member;
    let player = client.players.get(guild.id);
    if (!player) {
        player = new MusicPlayer(guild, interaction.channel, member.voice.channel);
        client.players.set(guild.id, player);
    }

    player.voiceChannel = member.voice.channel;
    player.textChannel = interaction.channel;

    if (!client.musicEmbedManager) client.musicEmbedManager = new MusicEmbedManager(client);
    const responseInteraction = options.responseInteraction === false ? null : interaction;
    return client.musicEmbedManager.handleMusicData(
        guild.id,
        {
            isPlaylist: options.isPlaylist || musicTracks.length > 1,
            tracks: musicTracks,
        },
        member,
        responseInteraction,
        interaction
    );
}

async function queueCatalogueTracks(interaction, client, catalogueTracks, options = {}) {
    if (!Array.isArray(catalogueTracks) || catalogueTracks.length === 0) {
        return { success: false, message: 'No catalogue tracks were found.' };
    }
    return queueMusicTracks(
        interaction,
        client,
        catalogueTracks.map(toMusicTrack),
        options
    );
}

module.exports = { toMusicTrack, validatePlayback, queueMusicTracks, queueCatalogueTracks };
