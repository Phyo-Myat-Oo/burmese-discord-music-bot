const { parentPort, workerData } = require('node:worker_threads');
const PhyuCatalog = require('./PhyuCatalog');

const catalogue = new PhyuCatalog(workerData.databasePath);

parentPort.on('message', message => {
    try {
        const items = catalogue.searchItems(message.query, { limit: message.limit });
        parentPort.postMessage({ id: message.id, items });
    } catch (error) {
        parentPort.postMessage({
            id: message.id,
            error: error instanceof Error ? error.message : String(error),
        });
    }
});
