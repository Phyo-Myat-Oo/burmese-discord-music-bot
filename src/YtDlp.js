const { execFile } = require('child_process');
const { promisify } = require('util');
const youtubeDlExec = require('youtube-dl-exec');

const execFileAsync = promisify(execFile);
const MAX_OUTPUT_BYTES = 64 * 1024 * 1024;

async function ytDlp(url, options = {}) {
    const binaryPath = youtubeDlExec.constants.YOUTUBE_DL_PATH;
    const args = [url, ...youtubeDlExec.args(options)];

    try {
        const { stdout } = await execFileAsync(binaryPath, args, {
            encoding: 'utf8',
            maxBuffer: MAX_OUTPUT_BYTES,
            windowsHide: true,
        });

        const output = stdout.trim();
        if (output.startsWith('{')) {
            return JSON.parse(output);
        }

        return output;
    } catch (error) {
        const detail = error.stderr?.trim();
        if (detail) {
            error.message = detail;
        }
        throw error;
    }
}

module.exports = ytDlp;
