const path = require('path');
const { Worker } = require('node:worker_threads');

class PhyuAutocomplete {
    constructor(databasePath = process.env.CATALOG_DB_PATH || 'data/daisy_v2/catalog.db') {
        this.nextRequestId = 1;
        this.pending = new Map();
        this.worker = new Worker(path.join(__dirname, 'phyuAutocompleteWorker.js'), {
            workerData: { databasePath: path.resolve(databasePath) },
        });

        this.worker.on('message', message => {
            const request = this.pending.get(message.id);
            if (!request) return;

            this.pending.delete(message.id);
            if (message.error) request.reject(new Error(message.error));
            else request.resolve(message.items);
        });

        this.worker.on('error', error => this.rejectAll(error));
        this.worker.on('exit', code => {
            if (code !== 0) this.rejectAll(new Error(`Phyu autocomplete worker exited with code ${code}.`));
        });

        // Discord's gateway keeps the bot alive; this worker should not prevent a clean shutdown.
        this.worker.unref();
    }

    request(method, ...args) {
        const id = this.nextRequestId++;
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            this.worker.postMessage({ id, method, args });
        });
    }

    search(query, options = {}) {
        return this.searchItems(query, { limit: options.limit || 25 });
    }

    searchItems(query, options = {}) {
        return this.request('searchItems', query, options);
    }

    searchTracks(query, options = {}) {
        return this.request('searchTracks', query, options);
    }

    listArtists(options = {}) {
        return this.request('listArtists', options);
    }

    searchArtists(query, options = {}) {
        return this.request('searchArtists', query, options);
    }

    getArtistById(id) {
        return this.request('getArtistById', id);
    }

    getAlbumsByArtist(id, options = {}) {
        return this.request('getAlbumsByArtist', id, options);
    }

    getAlbumById(id) {
        return this.request('getAlbumById', id);
    }

    getAlbumTracks(id) {
        return this.request('getAlbumTracks', id);
    }

    getAlbumTracksPage(id, options = {}) {
        return this.request('getAlbumTracksPage', id, options);
    }

    getTrackById(id) {
        return this.request('getTrackById', id);
    }

    getRandomTrack(options = {}) {
        return this.request('getRandomTrack', options);
    }

    rejectAll(error) {
        for (const request of this.pending.values()) request.reject(error);
        this.pending.clear();
    }
}

let sharedClient;
function getPhyuCatalogClient() {
    if (!sharedClient) sharedClient = new PhyuAutocomplete();
    return sharedClient;
}

module.exports = PhyuAutocomplete;
module.exports.getPhyuCatalogClient = getPhyuCatalogClient;
