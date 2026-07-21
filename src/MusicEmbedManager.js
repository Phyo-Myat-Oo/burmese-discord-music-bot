const { EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, MessageFlags } = require('discord.js');
const config = require('../config');
const LanguageManager = require('./LanguageManager');
const DaisyStateStore = require('./state/DaisyStateStore');

const SPECIAL_GREETING_USER_IDS = new Set([
    '1003981930305441853',
]);
const SPECIAL_GREETING = [
    'Hello , ငါရဲ့ ဒေစီလေး ရေ ဒီနေ့ ရောက်လာပေးလို့ ကျေးဇူးအများကြီး တင်ပါတယ် ။',
    'သီချင်းလေးတွေ နားထောင်ပြီး အေးချမ်းစွာအနားယူပါနော်  ။',
    '',
    'ဖြိုး။'
].join('\n');
const GREETING_TIME_ZONE = 'Asia/Yangon';

class MusicEmbedManager {
    constructor(client, options = {}) {
        this.client = client;
        this.stateStore = options.stateStore || null;
        this.now = options.now || (() => new Date());
        // Çakışma önleme için işlem kuyruğu
        this.processingQueue = new Map(); // guildId -> Promise
    }

    async translateOr(guildId, key, fallback, variables = {}) {
        const value = await LanguageManager.getTranslation(guildId, key, variables);
        return value === key ? fallback : value;
    }

    getRequesterText(track) {
        const requester = track.requestedBy || null;
        const requesterId = requester?.id || track.requesterId || null;
        if (requesterId) return `<@${requesterId}>`;
        return requester?.tag || requester?.user?.tag || track.requesterTag || 'Unknown';
    }

    getRepeatText(player) {
        if (player.loop === 'track') return 'Repeat: Track';
        if (player.loop === 'queue') return 'Repeat: Queue';
        return 'Repeat: Off';
    }

    getAutoplayText(player) {
        if (!player.autoplay) return 'Autoplay: Off';
        const modeLabels = {
            phyu_random: 'Phyu Random',
            rnb: 'R&B',
            hiphop: 'Hip-Hop',
            kpop: 'K-pop',
            lofi: 'Lo-fi',
        };
        const mode = modeLabels[player.autoplay]
            || String(player.autoplay).replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
        return `Autoplay: ${mode}`;
    }

    createProgressText(player, track) {
        const totalSeconds = Math.max(0, Math.floor(Number(track.duration) || 0));
        const currentSeconds = Math.max(0, Math.floor((Number(player.getCurrentTime?.()) || 0) / 1000));
        const clampedCurrent = totalSeconds > 0 ? Math.min(currentSeconds, totalSeconds) : currentSeconds;
        const barLength = 16;
        const filledLength = totalSeconds > 0
            ? Math.min(barLength, Math.max(0, Math.round((clampedCurrent / totalSeconds) * barLength)))
            : 0;
        const bar = '█'.repeat(filledLength) + '░'.repeat(barLength - filledLength);
        const totalText = totalSeconds > 0 ? this.formatDuration(totalSeconds) : 'Live';
        return `${this.formatDuration(clampedCurrent)} [${bar}] ${totalText}`;
    }

    /**
     * Müzik verilerini işler ve uygun embed'i gönderir/günceller
     */
    async handleMusicData(guildId, trackData, member, interaction = null, privateInteraction = interaction) {
        // Çakışma önleme - aynı guild için aynı anda sadece bir işlem
        if (this.processingQueue.has(guildId)) {
            await this.processingQueue.get(guildId);
        }

        const processingPromise = this._processMusic(guildId, trackData, member, interaction);
        this.processingQueue.set(guildId, processingPromise);

        try {
            const result = await processingPromise;
            if (result?.success) {
                await this.sendSpecialGreeting(privateInteraction, member);
            }
            return result;
        } finally {
            this.processingQueue.delete(guildId);
        }
    }

    async sendSpecialGreeting(interaction, member) {
        const userId = String(member?.id || '');
        if (!SPECIAL_GREETING_USER_IDS.has(userId)) return false;
        if (!interaction?.followUp) return false;

        const guildId = String(interaction.guildId || interaction.guild?.id || '');
        if (!guildId) return false;

        const localDate = this.getGreetingLocalDate(this.now());
        const stateStore = this.getStateStore();
        if (!stateStore.claimDailyGreeting(guildId, userId, localDate)) return false;

        try {
            await interaction.followUp({
                content: SPECIAL_GREETING,
                flags: MessageFlags.Ephemeral,
            });
            return true;
        } catch (error) {
            stateStore.releaseDailyGreeting(guildId, userId, localDate);
            console.error('Could not send the special greeting:', error.message);
            return false;
        }
    }

    getStateStore() {
        if (!this.stateStore) this.stateStore = new DaisyStateStore();
        return this.stateStore;
    }

    getGreetingLocalDate(date) {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone: GREETING_TIME_ZONE,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
        }).formatToParts(date);
        const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
        return `${values.year}-${values.month}-${values.day}`;
    }

    async _processMusic(guildId, trackData, member, interaction) {
        const player = this.client.players.get(guildId);
        if (!player) return { success: false, message: 'No player found' };

        const wasPlayingBefore = player.currentTrack !== null;
        const isPlaylist = trackData.isPlaylist || false;
        const tracks = trackData.tracks;

        try {
            let firstTrackResult = null;
            const wasIdle = (!player.currentTrack && player.queue.length === 0);

            // Tüm track'leri player'a ekle (preload'ı tetikleyecek)
            for (let i = 0; i < tracks.length; i++) {
                const track = { ...tracks[i] };
                track.requestedBy = member;
                track.addedAt = Date.now();

                // İlk track ve player boşsa
                if (i === 0 && wasIdle) {
                    player.currentTrack = track;

                    // Ses kanalına bağlan ve çalmaya başla
                    try {
                        if (!player.connection) {
                            await player.connect();
                        }
                        await player.play();

                        // Yeni embed oluştur
                        firstTrackResult = await this.createNewMusicEmbed(player, track, member, interaction);
                    } catch (playError) {
                        console.error('Error in play process:', playError);
                        // Hata durumunda track'i sıraya ekle
                        player.currentTrack = null;
                        player.queue.push(track);
                    }
                } else {
                    // Kuyruğa ekle
                    player.queue.push(track);
                }
            }

            // MusicPlayer owns the single sequential preload worker. It applies
            // source-specific limits so Phyu albums do not waste pCloud traffic.
            player.schedulePreloadWindow();

            // Eğer ilk şarkıyı çalmaya başladıysak ve playlist'te başka şarkılar varsa
            if (firstTrackResult && tracks.length > 1) {
                // Playlist'teki kalan şarkıları sıraya eklediğimizi bildiren mesaj göster
                await this.showPlaylistAdditionMessage(player, tracks, member, interaction, isPlaylist);
                // Kuyruk bilgisi güncellendi, embed'i de güncelle
                await this.updateNowPlayingEmbed(player);
                return firstTrackResult;
            }

            // Eğer sadece kuyruğa ekleme yaptıysak (zaten müzik çalıyordu)
            if (wasPlayingBefore || (!firstTrackResult && tracks.length > 0)) {
                return await this.handleQueueAddition(player, tracks, member, interaction, isPlaylist);
            }

            // Tek şarkı çalmaya başladıysak
            if (firstTrackResult) {
                return firstTrackResult;
            }

            return { success: true, message: 'Track processed successfully' };
        } catch (error) {
            return { success: false, message: 'Error processing music' };
        }
    }

    /**
     * Playlist ekleme mesajını gösterir (ilk şarkı çalıyorken kalan şarkıların eklendiğini bildirir)
     */
    async showPlaylistAdditionMessage(player, tracks, member, interaction, isPlaylist) {
        // Bilgi mesajı gönder (ilk şarkı hariç kalan şarkıları bildir)
        const remainingTracks = tracks.slice(1); // İlk şarkı hariç
        const messageText = await this.createQueueAdditionMessage(remainingTracks, member.guild.id, isPlaylist);

        // Mesajı text channel'a gönder (interaction değil)
        let infoMessage;
        try {
            infoMessage = await player.textChannel.send({ content: messageText });

            // Bilgi mesajını 10 saniye sonra sil
            setTimeout(async () => {
                try {
                    await infoMessage.delete();
                } catch (error) {
                    // Mesaj silinmiş olabilir
                }
            }, 10000);
        } catch (error) {
            console.error('Error sending playlist addition message:', error);
        }
    }

    /**
     * Yeni müzik embed'i oluşturur (çalan müzik yokken)
     */
    async createNewMusicEmbed(player, track, member, interaction) {
        player.requesterId = member.id;
        const embed = await this.createNowPlayingEmbed(player, track, member.guild.id);
        const buttons = await this.createControlButtons(player);

        let message;
        if (interaction) {
            if (interaction.deferred || interaction.replied) {
                message = await interaction.editReply({ content: null, embeds: [embed], components: buttons });
            } else {
                message = await interaction.reply({ embeds: [embed], components: buttons });
            }
        } else {
            message = await player.textChannel.send({ embeds: [embed], components: buttons });
        }

        player.nowPlayingMessage = message;
        return { success: true, message: 'Now playing', isNewEmbed: true };
    }

    async showNowPlayingCard(player, interaction) {
        const embed = await this.createNowPlayingEmbed(player, player.currentTrack, player.guild.id);
        const components = await this.createControlButtons(player);

        await interaction.reply({ embeds: [embed], components });
        const message = typeof interaction.fetchReply === 'function'
            ? await interaction.fetchReply()
            : null;
        if (message) player.nowPlayingMessage = message;

        return message;
    }

    /**
     * Kuyruğa şarkı eklenmesi durumunu yönetir
     */
    async handleQueueAddition(player, tracks, member, interaction, isPlaylist) {
        // Mevcut embed'i güncelle
        if (player.nowPlayingMessage && player.currentTrack) {
            await this.updateNowPlayingEmbed(player);
        }

        // Bilgi mesajı gönder
        const messageText = await this.createQueueAdditionMessage(tracks, member.guild.id, isPlaylist);

        let infoMessage;
        if (interaction) {
            if (interaction.deferred || interaction.replied) {
                infoMessage = await interaction.editReply({ content: messageText, embeds: [], components: [] });
            } else {
                infoMessage = await interaction.reply({ content: messageText, flags: [1 << 6] });
            }
        } else {
            infoMessage = await player.textChannel.send({ content: messageText });
        }

        // Bilgi mesajını 10 saniye sonra sil
        setTimeout(async () => {
            try {
                await infoMessage.delete();
            } catch (error) {
                // Mesaj silinmiş olabilir
            }
        }, 10000);

        return { success: true, message: 'Added to queue', isNewEmbed: false };
    }

    /**
     * Now Playing embed'ini oluşturur
     */
    async createNowPlayingEmbed(player, track, guildId) {
        const nowPlayingTitle = await LanguageManager.getTranslation(guildId, 'commands.play.now_playing');
        const sourceUrl = track.sourceUrl || track.url;

        const embed = new EmbedBuilder()
            .setTitle(nowPlayingTitle)
            .setDescription(`**[${track.title}](${sourceUrl})**`)
            .setColor(config.bot.embedColor)
            .setTimestamp();

        // Artist
        if (track.artist) {
            const artistLabel = await LanguageManager.getTranslation(guildId, 'commands.play.artist');
            embed.addFields({
                name: artistLabel,
                value: track.artist,
                inline: true
            });
        }

        // Duration
        if (track.duration) {
            const durationLabel = await LanguageManager.getTranslation(guildId, 'commands.play.duration');
            embed.addFields({
                name: durationLabel,
                value: this.formatDuration(track.duration),
                inline: true
            });
        }

        if (track.album) {
            const albumLabel = await this.translateOr(guildId, 'commands.nowplaying.album', 'Album');
            embed.addFields({
                name: albumLabel,
                value: track.album,
                inline: true
            });
        }

        // Platform
        if (track.platform) {
            const platformLabel = await LanguageManager.getTranslation(guildId, 'commands.play.platform');
            embed.addFields({
                name: platformLabel,
                value: this.getPlatformEmoji(track.platform) + ' ' +
                    track.platform.charAt(0).toUpperCase() + track.platform.slice(1),
                inline: true
            });
        }

        const requesterLabel = await this.translateOr(guildId, 'commands.nowplaying.requested_by', 'Requested by');
        embed.addFields({
            name: requesterLabel,
            value: this.getRequesterText(track),
            inline: true
        });

        const progressLabel = await this.translateOr(guildId, 'commands.nowplaying.progress', 'Progress');
        embed.addFields({
            name: progressLabel,
            value: this.createProgressText(player, track),
            inline: false
        });

        embed.addFields({
            name: 'Repeat',
            value: this.getRepeatText(player),
            inline: true
        });

        embed.addFields({
            name: 'Autoplay',
            value: this.getAutoplayText(player),
            inline: true
        });

        // Status
        const statusLabel = await LanguageManager.getTranslation(guildId, 'commands.nowplaying.status');
        const statusKey = player.paused
            ? 'commands.nowplaying.status_paused'
            : 'commands.nowplaying.status_playing';
        let statusValue = await LanguageManager.getTranslation(guildId, statusKey);

        if (player.pauseReasons && player.pauseReasons.has('mute')) {
            statusValue += ' 🔇';
        } else if (player.pauseReasons && player.pauseReasons.has('alone')) {
            statusValue += ' ⏳';
        }

        embed.addFields({
            name: statusLabel,
            value: statusValue,
            inline: true
        });

        // Thumbnail
        if (track.thumbnail) {
            embed.setThumbnail(track.thumbnail);
        }

        // Permission info and Queue info in footer
        const footerParts = [];
        
        // Playback controls are shared by everyone listening in the active voice channel.
        const permissionInfo = '🔓 Controls: Everyone in the active voice channel can control playback';
        footerParts.push(permissionInfo);
        
        // Add queue info if available
        if (player.queue.length > 0) {
            const queueInfo = await LanguageManager.getTranslation(guildId, 'commands.play.more_songs_in_queue', { count: player.queue.length });
            footerParts.push(queueInfo);
        }
        
        if (footerParts.length > 0) {
            embed.setFooter({ text: footerParts.join(' • ') });
        }

        return embed;
    }

    /**
     * Mevcut müzik embed'ini günceller
     */
    async updateNowPlayingEmbed(player) {
        if (!player.nowPlayingMessage || !player.currentTrack) return;

        try {
            const embed = await this.createNowPlayingEmbed(player, player.currentTrack, player.guild.id);
            const buttons = await this.createControlButtons(player);

            await player.nowPlayingMessage.edit({
                embeds: [embed],
                components: buttons
            });
        } catch (error) {
            console.error('Error updating now playing embed:', error);
        }
    }

    /**
     * Şarkı bittiğinde çağrılır
     */
    async handleTrackEnd(player) {
        if (player.queue.length > 0) {
            // Sıradaki şarkıya geç
            const nextTrack = player.queue.shift();
            player.currentTrack = nextTrack;

            await player.play();
            await this.updateNowPlayingEmbed(player);
        } else {
            // Tüm şarkılar bitti
            await this.handlePlaybackEnd(player);
        }
    }

    /**
     * Tüm müzikler bittiğinde çağrılır
     */
    async handlePlaybackEnd(player) {
        // Butonları devre dışı bırak
        if (player.nowPlayingMessage) {
            try {
                const disabledButtons = await this.createControlButtons(player, true);
                await player.nowPlayingMessage.edit({
                    components: disabledButtons
                });
            } catch (error) {
                console.error('Error disabling buttons:', error);
            }
        }

        let endEmbed = null;
        const guildId = player.guild?.id;

        try {
            const title = guildId
                ? await LanguageManager.getTranslation(guildId, 'musicmanager.playback_ended')
                : 'Playback Ended';
            const description = guildId
                ? await LanguageManager.getTranslation(guildId, 'musicmanager.queue_empty')
                : 'Queue is now empty.';

            endEmbed = new EmbedBuilder()
                .setTitle(`🎵 ${title}`)
                .setDescription(description)
                .setColor('#FF6B6B')
                .setTimestamp();
        } catch (error) {
            console.error('Error preparing playback end embed:', error);
        }

        if (!endEmbed) {
            endEmbed = new EmbedBuilder()
                .setDescription('🎵 Playback ended')
                .setColor('#FF6B6B')
                .setTimestamp();
        }

        const textChannel = player.textChannel;
        if (textChannel && typeof textChannel.send === 'function') {
            try {
                await textChannel.send({ embeds: [endEmbed] });
            } catch (error) {
                // Suppress errors when channel is unavailable or permissions are missing
            }
        }

        // Player'ı temizle
        player.currentTrack = null;
        player.nowPlayingMessage = null;
    }

    /**
     * Kontrol butonlarını oluşturur
     */
    async createControlButtons(player, disabled = false) {
        const guildId = player.guild.id;
        const sessionId = player.sessionId;
        const requesterId = player.requesterId;

        // Button labels
        const pauseLabel = player.paused ?
            await LanguageManager.getTranslation(guildId, 'buttons.resume') :
            await LanguageManager.getTranslation(guildId, 'buttons.pause');

        const skipLabel = await LanguageManager.getTranslation(guildId, 'buttons.skip');
        const previousLabel = await this.translateOr(guildId, 'buttons.previous', 'Previous');
        const stopLabel = await LanguageManager.getTranslation(guildId, 'buttons.stop');
        const queueLabel = await LanguageManager.getTranslation(guildId, 'buttons.queue');
        const shuffleLabel = await LanguageManager.getTranslation(guildId, 'buttons.shuffle');

        const previousButton = new ButtonBuilder()
            .setCustomId(`music_previous:${requesterId}:${sessionId}`)
            .setLabel(previousLabel)
            .setStyle(ButtonStyle.Secondary)
            .setEmoji('⏮️')
            .setDisabled(disabled || !player.previousTracks || player.previousTracks.length === 0);

        const pauseButton = new ButtonBuilder()
            .setCustomId(`music_pause:${requesterId}:${sessionId}`)
            .setLabel(pauseLabel)
            .setStyle(ButtonStyle.Secondary)
            .setEmoji(player.paused ? '▶️' : '⏸️')
            .setDisabled(disabled);

        const skipButton = new ButtonBuilder()
            .setCustomId(`music_skip:${requesterId}:${sessionId}`)
            .setLabel(skipLabel)
            .setStyle(ButtonStyle.Secondary)
            .setEmoji('⏭️')
            .setDisabled(disabled || player.queue.length === 0); // Sırada müzik yoksa disabled

        const stopButton = new ButtonBuilder()
            .setCustomId(`music_stop:${requesterId}:${sessionId}`)
            .setLabel(stopLabel)
            .setStyle(ButtonStyle.Danger)
            .setEmoji('⏹️')
            .setDisabled(disabled);

        const queueButton = new ButtonBuilder()
            .setCustomId(`music_queue:${requesterId}:${sessionId}`)
            .setLabel(queueLabel)
            .setStyle(ButtonStyle.Primary)
            .setEmoji('📋')
            .setDisabled(false); // Queue butonu her zaman aktif

        const shuffleButton = new ButtonBuilder()
            .setCustomId(`music_shuffle:${requesterId}:${sessionId}`)
            .setLabel(shuffleLabel)
            .setStyle(player.shuffle ? ButtonStyle.Success : ButtonStyle.Secondary)
            .setEmoji('🔀')
            .setDisabled(disabled);

        const volumeLabel = await LanguageManager.getTranslation(guildId, 'buttons.volume');
        const volumeButton = new ButtonBuilder()
            .setCustomId(`music_volume:${requesterId}:${sessionId}`)
            .setLabel(volumeLabel)
            .setStyle(ButtonStyle.Secondary)
            .setEmoji('🔊')
            .setDisabled(disabled);

        // Loop button - cycles through off -> track -> queue
        let loopLabel, loopEmoji, loopStyle;
        if (player.loop === 'track') {
            loopLabel = await LanguageManager.getTranslation(guildId, 'buttons.loop_track');
            loopEmoji = '🔂';
            loopStyle = ButtonStyle.Success;
        } else if (player.loop === 'queue') {
            loopLabel = await LanguageManager.getTranslation(guildId, 'buttons.loop_queue');
            loopEmoji = '🔁';
            loopStyle = ButtonStyle.Success;
        } else {
            loopLabel = await LanguageManager.getTranslation(guildId, 'buttons.loop_off');
            loopEmoji = '➡️';
            loopStyle = ButtonStyle.Secondary;
        }

        const loopButton = new ButtonBuilder()
            .setCustomId(`music_loop:${requesterId}:${sessionId}`)
            .setLabel(loopLabel)
            .setStyle(loopStyle)
            .setEmoji(loopEmoji)
            .setDisabled(disabled);

        // Autoplay button
        let autoplayLabel, autoplayEmoji, autoplayStyle;
        if (player.autoplay) {
            autoplayLabel = this.getAutoplayText(player);
            autoplayEmoji = '🎲';
            autoplayStyle = ButtonStyle.Success;
        } else {
            autoplayLabel = await LanguageManager.getTranslation(guildId, 'buttons.autoplay_off');
            autoplayEmoji = '🎲';
            autoplayStyle = ButtonStyle.Secondary;
        }

        const autoplayButton = new ButtonBuilder()
            .setCustomId(`music_autoplay:${requesterId}:${sessionId}`)
            .setLabel(autoplayLabel)
            .setStyle(autoplayStyle)
            .setEmoji(autoplayEmoji)
            .setDisabled(disabled);

        const favoriteButton = new ButtonBuilder()
            .setCustomId(`favorite:current-toggle:${sessionId}`)
            .setLabel('Favorite')
            .setStyle(ButtonStyle.Success)
            .setEmoji('⭐')
            .setDisabled(disabled || !player.currentTrack);

        const playlistButton = new ButtonBuilder()
            .setCustomId(`playlist:current:${sessionId}`)
            .setLabel('Playlist')
            .setStyle(ButtonStyle.Primary)
            .setEmoji('🎶')
            .setDisabled(disabled || !player.currentTrack);

        // Keep related controls on predictable rows, including on narrow Discord clients.
        const transportRow = new ActionRowBuilder()
            .addComponents(previousButton, pauseButton, skipButton);
        const sessionRow = new ActionRowBuilder()
            .addComponents(stopButton, queueButton);
        const playbackModeRow = new ActionRowBuilder()
            .addComponents(shuffleButton, loopButton, autoplayButton);
        const personalRow = new ActionRowBuilder()
            .addComponents(volumeButton, favoriteButton, playlistButton);

        return [transportRow, sessionRow, playbackModeRow, personalRow];
    }

    /**
     * Kuyruk ekleme mesajı oluşturur
     */
    async createQueueAdditionMessage(tracks, guildId, isPlaylist) {
        if (isPlaylist) {
            return await LanguageManager.getTranslation(guildId, 'musicmanager.playlist_added_to_queue', {
                count: tracks.length
            });
        } else {
            const track = tracks[0];
            const title = track?.title || 'Unknown Track';
            return await LanguageManager.getTranslation(guildId, 'musicmanager.track_added_to_queue', {
                title: title
            });
        }
    }

    /**
     * Duration formatı
     */
    formatDuration(seconds) {
        if (!seconds || seconds === 0) return '0:00';

        const totalSeconds = Math.floor(Number(seconds) || 0);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const remainingSeconds = totalSeconds % 60;

        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
        } else {
            return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
        }
    }

    /**
     * Platform emoji'si
     */
    getPlatformEmoji(platform) {
        if (platform === 'phyu') return '🌼';
        const emojis = {
            youtube: '🔴',
            spotify: '🟢',
            soundcloud: '🟠',
            direct: '🔗'
        };
        return emojis[platform] || '🎵';
    }
}

module.exports = MusicEmbedManager;
