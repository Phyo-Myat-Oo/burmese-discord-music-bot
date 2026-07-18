const {
    SlashCommandBuilder,
    EmbedBuilder,
    ActionRowBuilder,
    ButtonBuilder,
    ButtonStyle,
    MessageFlags,
} = require('discord.js');
const config = require('../config');

function buildHelpPayload(client) {
    const avatarUrl = client.user.displayAvatarURL();
    const embed = new EmbedBuilder()
        .setTitle('🌼 Daisy Music Bot အသုံးပြုနည်း')
        .setDescription([
            'Daisy က **Phyu Ni War Pyar သီချင်းများ** နဲ့ **YouTube သီချင်းများ** ကို Discord voice channel ထဲမှာ ဖွင့်ပေးနိုင်ပါတယ်။',
            'သီချင်းဖွင့်မယ့်သူက voice channel တစ်ခုထဲ အရင်ဝင်ထားပါ။',
        ].join('\n'))
        .setColor(config.bot.embedColor)
        .setThumbnail(avatarUrl)
        .addFields(
            {
                name: '🎼 Phyu Ni War Pyar သီချင်းများ',
                value: [
                    '`/phyu play query:` — သီချင်း၊ အဆိုတော် သို့မဟုတ် အယ်လ်ဘမ်အမည်ရိုက်ပြီး suggestion ကိုရွေးကာ ဖွင့်ရန်',
                    '`/phyu search query:` — ရှာဖွေမှုရလဒ်များကို စာမျက်နှာလိုက်ကြည့်ပြီး သီချင်းရွေးရန်',
                    'အယ်လ်ဘမ် suggestion ကိုရွေးလျှင် အယ်လ်ဘမ်တစ်ခုလုံး queue ထဲ ထည့်ပေးပါမယ်။',
                ].join('\n'),
                inline: false,
            },
            {
                name: '▶️ YouTube သီချင်းများ',
                value: [
                    '`/youtube play query:` — သီချင်းအမည် သို့မဟုတ် YouTube link ဖြင့် တိုက်ရိုက်ဖွင့်ရန်',
                    '`/youtube search query:` — စာမျက်နှာလိုက် YouTube ရလဒ်များထဲမှ သီချင်းတစ်ပုဒ် သို့မဟုတ် အများအပြားရွေးရန်',
                ].join('\n'),
                inline: false,
            },
            {
                name: '⭐ Favorites',
                value: [
                    '`/favorites add query:` — Phyu သီချင်းတစ်ပုဒ်ကို favorites ထဲသိမ်းရန်',
                    '`/favorites list` — သိမ်းထားသော သီချင်းများကိုကြည့်ပြီး ဖွင့်ရန်',
                    '`/favorites play query:` — သိမ်းထားသောသီချင်းကို အမြန်ဖွင့်ရန်',
                    '`/favorites remove query:` — favorites ထဲမှ ဖယ်ရှားရန်',
                    'Now Playing card ပေါ်က **Favorite** ခလုတ်နဲ့လည်း လက်ရှိသီချင်းကို သိမ်းနိုင်ပါတယ်။',
                ].join('\n'),
                inline: false,
            },
            {
                name: '🎛️ Player နှင့် Queue ခလုတ်များ',
                value: [
                    '**Previous · Pause/Resume · Skip · Stop · Queue**',
                    '**Shuffle · Volume · Repeat · Autoplay · Favorite**',
                    'Queue ထဲမှာ သီချင်းရွေးပြီး နေရာပြောင်းနိုင်သလို **Clear Queue** နဲ့ queue အားလုံးရှင်းနိုင်ပါတယ်။',
                    'Bot ရှိနေတဲ့ voice channel ထဲက နားထောင်သူတိုင်း playback ခလုတ်တွေကို အသုံးပြုနိုင်ပါတယ်။',
                ].join('\n'),
                inline: false,
            },
            {
                name: '🎲 Autoplay အသုံးပြုနည်း',
                value: [
                    '**Pop · Rock · R&B · K-pop · Random** စတဲ့ genre များက YouTube မှ သီချင်းရှာပြီး ဆက်ဖွင့်ပေးပါတယ်။',
                    '**Phyu Random Catalogue** က Phyu catalogue ထဲမှ ရနိုင်တဲ့သီချင်းကို ကျပန်းရွေးပြီး pCloud သို့မဟုတ် MediaFire ဖြင့်ဖွင့်ပါတယ်။',
                    'Queue လွတ်သွားချိန်မှာ autoplay စတင်ပြီး၊ queue ပြီးသွားမှရွေးထားလျှင်လည်း ချက်ချင်းစတင်ပါတယ်။',
                ].join('\n'),
                inline: false,
            },
            {
                name: 'ℹ️ အခြား command များ',
                value: [
                    '`/nowplaying` — လက်ရှိဖွင့်နေသော သီချင်းအချက်အလက်နှင့် progress ကိုကြည့်ရန်',
                    '`/help` — ဒီအသုံးပြုနည်းစာမျက်နှာကို ပြန်ဖွင့်ရန်',
                ].join('\n'),
                inline: false,
            },
            {
                name: '💡 အမြန်စတင်ရန်',
                value: 'Voice channel ထဲဝင်ပါ → `/phyu play` သို့မဟုတ် `/youtube play` သုံးပါ → ပေါ်လာတဲ့ player ခလုတ်များနဲ့ ထိန်းချုပ်ပါ။',
                inline: false,
            },
        )
        .setFooter({
            text: `${client.user.username} • မြန်မာသီချင်းများအတွက် Discord Music Bot`,
            iconURL: avatarUrl,
        })
        .setTimestamp();

    const controls = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
            .setCustomId('help_refresh')
            .setLabel('ပြန်လည်ဖော်ပြရန်')
            .setEmoji('🔄')
            .setStyle(ButtonStyle.Secondary)
    );

    return { embeds: [embed], components: [controls] };
}

module.exports = {
    data: new SlashCommandBuilder()
        .setName('help')
        .setDescription('Daisy bot command များနှင့် အသုံးပြုနည်းကို မြန်မာလိုပြပါမယ်'),

    buildHelpPayload,

    async execute(interaction, client) {
        try {
            await interaction.reply(buildHelpPayload(client));
        } catch (error) {
            console.error('[Help] Could not show help:', error);
            const response = {
                content: '❌ အသုံးပြုနည်းကို မဖော်ပြနိုင်သေးပါ။ ခဏနေရင် ထပ်စမ်းကြည့်ပါ။',
                flags: MessageFlags.Ephemeral,
            };

            if (interaction.deferred || interaction.replied) {
                await interaction.followUp(response);
            } else {
                await interaction.reply(response);
            }
        }
    },
};
