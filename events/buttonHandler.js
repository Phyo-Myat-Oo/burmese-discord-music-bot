const { Events, EmbedBuilder, ActionRowBuilder, ModalBuilder, TextInputBuilder, TextInputStyle, ButtonBuilder, ButtonStyle } = require('discord.js');
const config = require('../config');
const LanguageManager = require('../src/LanguageManager');
const MusicPlayer = require('../src/MusicPlayer');
const { PAGE_SIZE, createQueueView } = require('../src/QueueView');

module.exports = {
    name: Events.InteractionCreate,
    async execute(interaction) {
        if (!interaction.isButton() && !interaction.isStringSelectMenu()) return;

        const client = interaction.client;
        const guild = interaction.guild;
        const member = interaction.member;

        // Feature-specific components are handled by their own event modules.
        if (interaction.customId.startsWith('phyu:') || interaction.customId.startsWith('favorite:')) return;

        // Special controls for search buttons
        if (interaction.isButton() && interaction.customId.startsWith('search_')) {
            return await this.handleSearchInteraction(interaction, client);
        }

        // Help refresh button (doesn't require voice channel)
        if (interaction.isButton() && interaction.customId === 'help_refresh') {
            return await this.handleHelpRefresh(interaction);
        }

        // Check if user is in a voice channel (for music controls)
        if (!member.voice.channel) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.voice_channel_required'),
                flags: [1 << 6]
            });
        }

        // Get music player
        const player = client.players.get(guild.id);
        if (!player) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.no_music_playing'),
                flags: [1 << 6]
            });
        }

        // Check if user is in the same voice channel as bot
        if (player.voiceChannel.id !== member.voice.channel.id) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.same_channel_required'),
                flags: [1 << 6]
            });
        }

        try {
            // Parse custom ID for authorization and session validation
            const customIdParts = interaction.customId.split(':');
            const [buttonType, requesterId, sessionId] = customIdParts;

            // Session validation for authorized buttons (skip queue button)
            if (sessionId && player.sessionId && sessionId !== player.sessionId) {
                return await interaction.reply({
                    content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.session_invalid'),
                    flags: [1 << 6]
                });
            }

            switch (buttonType) {
                case 'music_pause':
                    await this.handlePause(interaction, player, requesterId);
                    break;

                case 'music_skip':
                    await this.handleSkip(interaction, player, requesterId);
                    break;

                case 'music_previous':
                    await this.handlePrevious(interaction, player, requesterId);
                    break;

                case 'music_stop':
                    await this.handleStop(interaction, player, client, requesterId);
                    break;

                case 'music_queue':
                    await this.handleQueue(interaction, player);
                    break;

                case 'music_queue_pick':
                    await this.handleQueuePick(interaction, player, requesterId, sessionId, customIdParts);
                    break;

                case 'music_queue_move':
                    await this.handleQueueMove(interaction, player, requesterId, sessionId, customIdParts);
                    break;

                case 'music_queue_page':
                    await this.handleQueuePage(interaction, player, customIdParts);
                    break;

                case 'music_queue_clear':
                    await this.handleQueueClear(interaction, player, requesterId);
                    break;

                case 'music_queue_back':
                    await this.handleQueueBack(interaction, player);
                    break;

                case 'music_shuffle':
                    await this.handleShuffle(interaction, player, requesterId);
                    break;

                case 'music_volume':
                    await this.handleVolumeModal(interaction, player, requesterId);
                    break;

                case 'music_loop':
                    await this.handleLoop(interaction, player, requesterId);
                    break;

                case 'music_autoplay':
                    await this.handleAutoplay(interaction, player, requesterId);
                    break;

                case 'music_lyrics':
                    await this.handleLyrics(interaction, player);
                    break;

                default:
                    await interaction.reply({
                        content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.unknown_interaction'),
                        flags: [1 << 6]
                    });
            }
        } catch (error) {

            if (!interaction.replied && !interaction.deferred) {
                try {
                    await interaction.reply({
                        content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.processing_error'),
                        flags: [1 << 6]
                    });
                } catch (replyError) {
                }
            }
        }
    },

    // Authorization control function
    isAuthorized(interaction, requesterId) {
        const member = interaction.member;

        // ManageGuild permission check (Sunucuyu Yönet)
        if (member.permissions.has('ManageGuild')) return true;

        // DJ role check (if exists)
        if (member.roles.cache.some(role => role.name.toLowerCase().includes('dj'))) return true;

        // Music starter check
        if (member.id === requesterId) return true;

        return false;
    },

    async handlePause(interaction, player, requesterId) {

        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        if (!player.currentTrack) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_song_playing'),
                flags: [1 << 6]
            });
        }

        let result;
        let message;
        let emoji;

        if (player.paused) {
            result = player.resume();
            message = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.music_resumed');
            emoji = '▶️';
        } else {
            result = player.pause();
            message = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.music_paused');
            emoji = '⏸️';
        }

        if (result) {
            const actionByLabel = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.action_by');
            const embed = new EmbedBuilder()
                .setTitle(`${emoji} ${message}`)
                .setDescription(`**[${player.currentTrack.title}](${player.currentTrack.url})** ${message}!`)
                .setColor(config.bot.embedColor)
                .setTimestamp()
                .addFields({
                    name: actionByLabel,
                    value: `${interaction.member}`,
                    inline: true
                });

            if (player.currentTrack.thumbnail) {
                embed.setThumbnail(player.currentTrack.thumbnail);
            }

            await interaction.reply({ embeds: [embed], flags: [1 << 6] });

            // Ana embed'deki butonları güncelle (pause/resume değişimi)
            if (interaction.client.musicEmbedManager) {
                await interaction.client.musicEmbedManager.updateNowPlayingEmbed(player);
            }
        } else {
            await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.operation_failed'),
                flags: [1 << 6]
            });
        }
    },

    async handleSkip(interaction, player, requesterId) {
        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        if (!player.currentTrack) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_song_playing'),
                flags: [1 << 6]
            });
        }

        // Sırada müzik yoksa atlanamaz
        if (player.queue.length === 0) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_songs_to_skip'),
                flags: [1 << 6]
            });
        }

        const currentTrack = player.currentTrack;
        const skipped = player.skip();

        if (skipped) {
            const embed = new EmbedBuilder()
                .setTitle(await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.song_skipped_title'))
                .setDescription(`**[${currentTrack.title}](${currentTrack.url})** ${await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.skipped')}!`)
                .setColor(config.bot.embedColor)
                .setTimestamp()
                .addFields({
                    name: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.skipped_by'),
                    value: `${interaction.member}`,
                    inline: true
                });

            if (player.queue.length > 0) {
                embed.addFields({
                    name: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.next_song'),
                    value: `[${player.queue[0].title}](${player.queue[0].url})`,
                    inline: false
                });
                embed.setFooter({
                    text: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.more_songs_in_queue', { count: player.queue.length })
                });
            } else {
                embed.setFooter({
                    text: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_more_songs')
                });
            }

            if (currentTrack.thumbnail) {
                embed.setThumbnail(currentTrack.thumbnail);
            }

            await interaction.reply({ embeds: [embed], flags: [1 << 6] });

            // Embed Manager ile ana embed'i güncelle
            if (interaction.client.musicEmbedManager && player.currentTrack) {
                await interaction.client.musicEmbedManager.updateNowPlayingEmbed(player);
            }
        } else {
            await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.song_not_skipped'),
                flags: [1 << 6]
            });
        }
    },

    async handlePrevious(interaction, player, requesterId) {
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        if (!player.currentTrack) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_song_playing'),
                flags: [1 << 6]
            });
        }

        if (!player.previousTracks || player.previousTracks.length === 0) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_previous_song'),
                flags: [1 << 6]
            });
        }

        const result = await player.previous();

        if (result) {
            await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.moved_to_previous'),
                flags: [1 << 6]
            });
            if (interaction.client.musicEmbedManager && player.currentTrack) {
                await interaction.client.musicEmbedManager.updateNowPlayingEmbed(player);
            }
        } else {
            await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.previous_failed'),
                flags: [1 << 6]
            });
        }
    },

    async handleStop(interaction, player, client, requesterId) {
        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        const queueLength = player.queue.length;
        const currentTrack = player.currentTrack;

        player.stop();
        client.players.delete(interaction.guild.id);

        const embed = new EmbedBuilder()
            .setTitle(await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.music_stopped_title'))
            .setDescription(`${currentTrack ? `**[${currentTrack.title}](${currentTrack.url})**` : 'Music'} ${await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.stopped')}!`)
            .setColor('#FF0000')
            .setTimestamp()
            .addFields({
                name: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.stopped_by'),
                value: `${interaction.member}`,
                inline: true
            });

        if (queueLength > 0) {
            embed.setFooter({
                text: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.songs_cleared', { count: queueLength })
            });
        }

        await interaction.reply({ embeds: [embed], flags: [1 << 6] });

        // Ana embed'deki butonları disable yap
        if (client.musicEmbedManager) {
            await client.musicEmbedManager.handlePlaybackEnd(player);
        }
    },

    async handleQueue(interaction, player) {
        await interaction.update(createQueueView(player));
    },

    async handleQueuePick(interaction, player, requesterId, sessionId, customIdParts) {
        if (!this.isAuthorized(interaction, requesterId)) {
            return interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        const page = Number(customIdParts[3]) || 0;
        const selectedIndex = Number(interaction.values[0]);
        await interaction.update(createQueueView(player, { page, selectedIndex }));
    },

    async handleQueueMove(interaction, player, requesterId, sessionId, customIdParts) {
        if (!this.isAuthorized(interaction, requesterId)) {
            return interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        const from = Number(customIdParts[4]);
        const to = Number(interaction.values[0]);
        if (!Number.isInteger(from) || !Number.isInteger(to) || !player.moveInQueue(from, to)) {
            return interaction.reply({ content: 'That queue position is no longer available.', flags: [1 << 6] });
        }

        const page = Math.floor(to / PAGE_SIZE);
        await interaction.update(createQueueView(player, { page, selectedIndex: to }));
    },

    async handleQueuePage(interaction, player, customIdParts) {
        const page = Number(customIdParts[3]) || 0;
        const selectedIndex = Number(customIdParts[4]);
        await interaction.update(createQueueView(player, { page, selectedIndex }));
    },

    async handleQueueClear(interaction, player, requesterId) {
        if (!this.isAuthorized(interaction, requesterId)) {
            return interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        player.clearQueue();
        await interaction.update(createQueueView(player));
    },

    async handleQueueBack(interaction, player) {
        const manager = interaction.client.musicEmbedManager;
        if (!manager || !player.currentTrack) {
            return interaction.reply({ content: 'There is no current playback card.', flags: [1 << 6] });
        }

        const embed = await manager.createNowPlayingEmbed(player, player.currentTrack, player.guild.id);
        const components = await manager.createControlButtons(player);
        await interaction.update({ embeds: [embed], components });
    },

    async handleShuffle(interaction, player, requesterId) {
        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        if (player.queue.length < 2) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.minimum_songs_shuffle'),
                flags: [1 << 6]
            });
        }

        // Shuffle the queue
        player.shuffleQueue();

        const shuffleTitle = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.queue_shuffled_title');
        const shuffleDesc = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.songs_shuffled', { count: player.queue.length });
        const shuffledByLabel = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.shuffled_by');

        const embed = new EmbedBuilder()
            .setTitle(shuffleTitle)
            .setDescription(shuffleDesc)
            .setColor(config.bot.embedColor)
            .setTimestamp()
            .addFields({
                name: shuffledByLabel,
                value: `${interaction.member}`,
                inline: true
            });

        // Show first few shuffled tracks
        if (player.queue.length > 0) {
            const nextTracks = player.queue.slice(0, 3);
            let trackList = '';
            nextTracks.forEach((track, index) => {
                trackList += `${index + 1}. **[${track.title}](${track.url})**\n`;
            });

            const nextSongsLabel = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.next_songs');
            embed.addFields({
                name: nextSongsLabel,
                value: trackList,
                inline: false
            });
        }

        await interaction.reply({ embeds: [embed], flags: [1 << 6] });

        // Ana embed'i güncelle
        if (interaction.client.musicEmbedManager) {
            await interaction.client.musicEmbedManager.updateNowPlayingEmbed(player);
        }
    },

    async handleVolumeModal(interaction, player, requesterId) {
        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        const volumeTitle = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.set_volume_title');
        const volumeLabel = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.volume_label');

        const modal = new ModalBuilder()
            .setCustomId('volume_modal')
            .setTitle(volumeTitle);

        const volumeInput = new TextInputBuilder()
            .setCustomId('volume_input')
            .setLabel(volumeLabel)
            .setStyle(TextInputStyle.Short)
            .setMinLength(1)
            .setMaxLength(3)
            .setPlaceholder('50')
            .setRequired(true);

        const actionRow = new ActionRowBuilder().addComponents(volumeInput);
        modal.addComponents(actionRow);

        await interaction.showModal(modal);
    },

    async handleLoop(interaction, player, requesterId) {
        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        if (!player.currentTrack) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.no_song_playing'),
                flags: [1 << 6]
            });
        }

        // Cycle through loop modes: false -> 'track' -> 'queue' -> false
        let newLoopMode;
        let modeMessage;
        let modeEmoji;

        if (player.loop === false || player.loop === 'off') {
            newLoopMode = 'track';
            modeMessage = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.loop_mode_track');
            modeEmoji = '🔂';
        } else if (player.loop === 'track') {
            newLoopMode = 'queue';
            modeMessage = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.loop_mode_queue');
            modeEmoji = '🔁';
        } else {
            newLoopMode = false;
            modeMessage = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.loop_mode_off');
            modeEmoji = '➡️';
        }

        // Update player loop mode
        player.loop = newLoopMode;

        const loopTitle = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.loop_mode_changed_title');
        const changedByLabel = await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.changed_by');

        const embed = new EmbedBuilder()
            .setTitle(`${modeEmoji} ${loopTitle}`)
            .setDescription(modeMessage)
            .setColor(config.bot.embedColor)
            .setTimestamp()
            .addFields({
                name: changedByLabel,
                value: `${interaction.member}`,
                inline: true
            });

        if (player.currentTrack && player.currentTrack.thumbnail) {
            embed.setThumbnail(player.currentTrack.thumbnail);
        }

        await interaction.reply({ embeds: [embed], flags: [1 << 6] });

        // Update the main embed to reflect the new loop mode
        if (interaction.client.musicEmbedManager) {
            await interaction.client.musicEmbedManager.updateNowPlayingEmbed(player);
        }
    },

    async handleAutoplay(interaction, player, requesterId) {
        const { StringSelectMenuBuilder, StringSelectMenuOptionBuilder, ActionRowBuilder } = require('discord.js');
        
        // Authorization check
        if (!this.isAuthorized(interaction, requesterId)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.not_authorized'),
                flags: [1 << 6]
            });
        }

        // If autoplay is already enabled, turn it off
        if (player.autoplay) {
            player.autoplay = false;
            
            const embed = new EmbedBuilder()
                .setTitle('🎲 ' + await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.autoplay_disabled'))
                .setDescription(await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.autoplay_disabled_desc'))
                .setColor(config.bot.embedColor)
                .setTimestamp();

            await interaction.reply({ embeds: [embed], flags: [1 << 6] });
            
            // Update the main embed
            if (interaction.client.musicEmbedManager) {
                await interaction.client.musicEmbedManager.updateNowPlayingEmbed(player);
            }
            return;
        }

        // Show genre selection menu
        const select = new StringSelectMenuBuilder()
            .setCustomId(`autoplay_genre:${requesterId}:${player.sessionId}`)
            .setPlaceholder(await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.autoplay_select_genre'))
            .addOptions(
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.pop'))
                    .setValue('pop')
                    .setEmoji('🎤'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.rock'))
                    .setValue('rock')
                    .setEmoji('🎸'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.hiphop'))
                    .setValue('hiphop')
                    .setEmoji('🎧'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.electronic'))
                    .setValue('electronic')
                    .setEmoji('🎛️'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.jazz'))
                    .setValue('jazz')
                    .setEmoji('🎷'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.classical'))
                    .setValue('classical')
                    .setEmoji('🎻'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.metal'))
                    .setValue('metal')
                    .setEmoji('🤘'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.country'))
                    .setValue('country')
                    .setEmoji('🤠'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.rnb'))
                    .setValue('rnb')
                    .setEmoji('💃'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.indie'))
                    .setValue('indie')
                    .setEmoji('🌿'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.latin'))
                    .setValue('latin')
                    .setEmoji('💃'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.kpop'))
                    .setValue('kpop')
                    .setEmoji('🇰🇷'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.anime'))
                    .setValue('anime')
                    .setEmoji('🎌'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.lofi'))
                    .setValue('lofi')
                    .setEmoji('🌙'),
                new StringSelectMenuOptionBuilder()
                    .setLabel(await LanguageManager.getTranslation(interaction.guild?.id, 'genres.random'))
                    .setValue('random')
                    .setEmoji('🎲')
            );

        const row = new ActionRowBuilder().addComponents(select);

        const embed = new EmbedBuilder()
            .setTitle('🎲 ' + await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.autoplay_select_title'))
            .setDescription(await LanguageManager.getTranslation(interaction.guild?.id, 'buttonhandler.autoplay_select_desc'))
            .setColor(config.bot.embedColor);

        await interaction.reply({
            embeds: [embed],
            components: [row],
            flags: [1 << 6]
        });
    },

    createProgressBar(current, total) {
        if (!total || total === 0) return '0:00 / 0:00';

        const currentSeconds = Math.floor(current / 1000);
        const totalSeconds = Math.floor(total);
        const progress = Math.floor((currentSeconds / totalSeconds) * 20);

        const bar = '█'.repeat(progress) + '░'.repeat(20 - progress);

        return `${this.formatTime(currentSeconds)} [${'▓'.repeat(progress)}${'░'.repeat(20 - progress)}] ${this.formatTime(totalSeconds)}`;
    },

    formatTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;

        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        } else {
            return `${minutes}:${secs.toString().padStart(2, '0')}`;
        }
    },

    async handleHelpRefresh(interaction) {
        try {
            await interaction.deferUpdate();
            const helpCommand = require('../commands/help');
            await interaction.editReply(helpCommand.buildHelpPayload(interaction.client));
        } catch (error) {
            console.error('[Help] Could not refresh help:', error);
            try {
                if (!interaction.replied && !interaction.deferred) {
                    await interaction.reply({
                        content: '❌ အသုံးပြုနည်းကို ပြန်မဖော်ပြနိုင်သေးပါ။',
                        flags: [1 << 6]
                    });
                } else {
                    await interaction.followUp({
                        content: '❌ အသုံးပြုနည်းကို ပြန်မဖော်ပြနိုင်သေးပါ။',
                        flags: [1 << 6]
                    });
                }
            } catch (err) {
                console.error('Failed to send error message:', err);
            }
        }
    },

    async handleSearchInteraction(interaction, client) {
        const member = interaction.member;
        const guild = interaction.guild;

        // Check if user is in a voice channel
        if (!member.voice.channel) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.voice_channel_required'),
                flags: [1 << 6]
            });
        }

        // Check search results
        if (!global.searchResults || !global.searchResults.has(interaction.user.id)) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.search_expired'),
                flags: [1 << 6]
            });
        }

        const userSearchData = global.searchResults.get(interaction.user.id);

        if (interaction.customId === 'search_cancel') {
            global.searchResults.delete(interaction.user.id);

            const embed = new EmbedBuilder()
                .setTitle(await LanguageManager.getTranslation(guild?.id, 'buttonhandler.search_cancelled_title'))
                .setDescription(await LanguageManager.getTranslation(guild?.id, 'buttonhandler.search_cancelled_desc'))
                .setColor('#FF0000')
                .setTimestamp();

            return await interaction.update({
                embeds: [embed],
                components: []
            });
        }

        // Get selected song index
        const selectedIndex = parseInt(interaction.customId.replace('search_select_', ''));
        const selectedTrack = userSearchData.results[selectedIndex];

        if (!selectedTrack) {
            return await interaction.reply({
                content: await LanguageManager.getTranslation(guild?.id, 'buttonhandler.invalid_selection'),
                flags: [1 << 6]
            });
        }

        await interaction.deferUpdate();

        // Işlem mesajı göster
        const processingEmbed = new EmbedBuilder()
            .setTitle('🔄 ' + await LanguageManager.getTranslation(guild?.id, 'buttonhandler.processing'))
            .setDescription(await LanguageManager.getTranslation(guild?.id, 'buttonhandler.adding_song_desc', { title: selectedTrack.title }))
            .setColor('#FFAA00')
            .setTimestamp();

        await interaction.editReply({
            embeds: [processingEmbed],
            components: []
        });

        try {
            // Embed Manager ile işle
            const MusicEmbedManager = require('../src/MusicEmbedManager');
            if (!client.musicEmbedManager) {
                client.musicEmbedManager = new MusicEmbedManager(client);
            }

            // Ensure music player exists and is configured
            if (!client.players) {
                client.players = new Map();
            }

            let player = client.players.get(guild.id);
            if (!player) {
                player = new MusicPlayer(guild, interaction.channel, member.voice.channel);
                client.players.set(guild.id, player);
            }

            player.voiceChannel = member.voice.channel;
            player.textChannel = interaction.channel;

            // Seçilen şarkıyı işle
            const result = await client.musicEmbedManager.handleMusicData(
                guild.id,
                {
                    isPlaylist: false,
                    tracks: [selectedTrack]
                },
                member,
                interaction
            );

            // Search results temizle
            global.searchResults.delete(interaction.user.id);

            if (!result.success) {
                const errorEmbed = new EmbedBuilder()
                    .setTitle('❌ ' + await LanguageManager.getTranslation(guild?.id, 'buttonhandler.error_title'))
                    .setDescription(result.message)
                    .setColor('#FF0000')
                    .setTimestamp();

                return await interaction.editReply({
                    embeds: [errorEmbed],
                    components: []
                });
            }

        } catch (error) {
            const errorEmbed = new EmbedBuilder()
                .setTitle('❌ ' + await LanguageManager.getTranslation(guild?.id, 'buttonhandler.error_title'))
                .setDescription(await LanguageManager.getTranslation(guild?.id, 'buttonhandler.processing_error'))
                .setColor('#FF0000')
                .setTimestamp();

            await interaction.editReply({
                embeds: [errorEmbed],
                components: []
            });
        }
    },

    async handleLyrics(interaction, player) {
        const LyricsManager = require('../src/LyricsManager');
        const guildId = interaction.guild?.id;

        try {
            if (!player.currentTrack) {
                return await interaction.reply({
                    content: await LanguageManager.getTranslation(guildId, 'buttonhandler.no_song_playing'),
                    flags: [1 << 6]
                });
            }

            if (!player.hasLyrics || !player.hasLyrics()) {
                const noLyricsMsg = await LanguageManager.getTranslation(guildId, 'buttonhandler.no_lyrics_found') || 'No lyrics found for this song.';
                return await interaction.reply({
                    content: `🎤 ${noLyricsMsg}`,
                    flags: [1 << 6]
                });
            }

            await interaction.deferReply({ ephemeral: true });

            const lyricsData = player.currentLyrics;
            const pages = LyricsManager.formatFullLyrics(lyricsData, 4000);

            if (pages.length === 0) {
                return await interaction.editReply({
                    content: await LanguageManager.getTranslation(guildId, 'buttonhandler.lyrics_unavailable') || 'Lyrics are unavailable.'
                });
            }

            const lyricsTitle = await LanguageManager.getTranslation(guildId, 'buttonhandler.lyrics_title') || 'Song Lyrics';
            
            // If only one page, send directly
            if (pages.length === 1) {
                const embed = new EmbedBuilder()
                    .setTitle(`🎤 ${lyricsTitle}`)
                    .setDescription(`**${player.currentTrack.title}**\n${player.currentTrack.artist ? `*by ${player.currentTrack.artist}*\n` : ''}\n${pages[0]}`)
                    .setColor(config.bot.embedColor)
                    .setFooter({ text: `Source: ${lyricsData.source}` })
                    .setTimestamp();

                return await interaction.editReply({ embeds: [embed] });
            }

            // Multiple pages - send with pagination buttons
            let currentPage = 0;

            const createLyricsEmbed = (pageIndex) => {
                return new EmbedBuilder()
                    .setTitle(`🎤 ${lyricsTitle}`)
                    .setDescription(`**${player.currentTrack.title}**\n${player.currentTrack.artist ? `*by ${player.currentTrack.artist}*\n` : ''}\n${pages[pageIndex]}`)
                    .setColor(config.bot.embedColor)
                    .setFooter({ text: `Source: ${lyricsData.source} | Page ${pageIndex + 1}/${pages.length}` })
                    .setTimestamp();
            };

            const createPaginationButtons = (pageIndex) => {
                const prevButton = new ButtonBuilder()
                    .setCustomId('lyrics_prev')
                    .setLabel('◀ Previous')
                    .setStyle(ButtonStyle.Secondary)
                    .setDisabled(pageIndex === 0);

                const nextButton = new ButtonBuilder()
                    .setCustomId('lyrics_next')
                    .setLabel('Next ▶')
                    .setStyle(ButtonStyle.Secondary)
                    .setDisabled(pageIndex === pages.length - 1);
                return new ActionRowBuilder().addComponents(prevButton, nextButton);
            };

            await interaction.editReply({
                embeds: [createLyricsEmbed(currentPage)],
                components: [createPaginationButtons(currentPage)]
            });

            // Fetch the reply message for the collector
            const message = await interaction.fetchReply();

            // Collector for pagination
            const collector = message.createMessageComponentCollector({
                filter: i => i.user.id === interaction.user.id,
                time: 300000 // 5 minutes
            });

            collector.on('collect', async i => {
                try {
                    // Log to see what's happening
                    console.log('🔍 Button clicked:', i.customId, 'by', i.user.tag);
                    console.log('🔍 Current page:', currentPage, 'Total pages:', pages.length);

                    if (i.customId === 'lyrics_prev' && currentPage > 0) {
                        currentPage--;
                    } else if (i.customId === 'lyrics_next' && currentPage < pages.length - 1) {
                        currentPage++;
                    }

                    console.log('🔍 New page:', currentPage);

                    // Try deferUpdate first, then update
                    if (!i.deferred && !i.replied) {
                        await i.deferUpdate();
                    }

                    await message.edit({
                        embeds: [createLyricsEmbed(currentPage)],
                        components: [createPaginationButtons(currentPage)]
                    });

                } catch (error) {
                    console.error('❌ Pagination error details:', {
                        code: error.code,
                        message: error.message,
                        deferred: i.deferred,
                        replied: i.replied
                    });
                    
                    // Ignore interaction timeout/unknown interaction errors
                    if (error.code === 10062 || error.code === 10008 || error.code === 40060) {
                        console.log('ℹ️ Interaction expired or unknown, ignoring...');
                    }
                }
            });

            collector.on('end', () => {
                interaction.editReply({ components: [] }).catch(() => {});
            });

        } catch (error) {
            console.error('❌ Lyrics handler error:', error);
            const errorMsg = await LanguageManager.getTranslation(guildId, 'buttonhandler.lyrics_error') || 'Failed to load lyrics.';
            if (interaction.deferred) {
                await interaction.editReply({ content: errorMsg });
            } else {
                await interaction.reply({ content: errorMsg, ephemeral: true });
            }
        }
    }
};
