const { parentPort, workerData } = require('node:worker_threads');
const PhyuCatalog = require('./PhyuCatalog');

const catalogue = new PhyuCatalog(workerData.databasePath);
const ALLOWED_METHODS = new Set([
    'searchItems',
    'searchTracks',
    'listArtists',
    'searchArtists',
    'getArtistById',
    'getAlbumsByArtist',
    'getAlbumById',
    'getAlbumTracks',
    'getAlbumTracksPage',
    'getTrackById',
    'getRandomTrack',
]);

parentPort.on('message', message => {
    try {
        if (!ALLOWED_METHODS.has(message.method)) {
            throw new Error(`Unsupported catalogue worker method: ${message.method}`);
        }
        const items = catalogue[message.method](...(message.args || []));
        parentPort.postMessage({ id: message.id, items });
    } catch (error) {
        parentPort.postMessage({
            id: message.id,
            error: error instanceof Error ? error.message : String(error),
        });
    }
});
