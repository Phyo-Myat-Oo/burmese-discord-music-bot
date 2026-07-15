const fs = require('fs');

function resolveFFmpegPath() {
    const configuredPath = process.env.FFMPEG_PATH?.trim();
    if (configuredPath) {
        return configuredPath;
    }

    try {
        const bundledPath = require('ffmpeg-static');
        if (bundledPath && fs.existsSync(bundledPath)) {
            return bundledPath;
        }
    } catch {
        // ffmpeg-static is optional; fall through to the system executable.
    }

    return 'ffmpeg';
}

module.exports = resolveFFmpegPath();
