#!/usr/bin/env node
const path = require('node:path');
const { CatalogueUpdater } = require('../src/catalog/CatalogueUpdater');

function optionValue(args, name, fallback) {
    const prefix = `--${name}=`;
    const inline = args.find(argument => argument.startsWith(prefix));
    if (inline) return inline.slice(prefix.length);
    const index = args.indexOf(`--${name}`);
    return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

function printHelp() {
    console.log(`Daisy catalogue updater

Usage:
  node scripts/update-catalogue.js [options]

Options:
  --db PATH                 Catalogue database (default: CATALOG_DB_PATH)
  --recent-posts NUMBER     Recent/edited Blogspot posts to inspect (default: 100)
  --backfill-posts NUMBER   Existing posts missing MediaFire keys (default: 50)
  --backup                  Keep a safe SQLite backup (latest three retained)
  --dry-run                 Fetch and report without changing the catalogue
  --help                    Show this help
`);
}

async function main() {
    const args = process.argv.slice(2);
    if (args.includes('--help')) return printHelp();
    const databasePath = path.resolve(optionValue(
        args,
        'db',
        process.env.CATALOG_DB_PATH || 'data/daisy_v2/catalog.db'
    ));
    const updater = new CatalogueUpdater({
        databasePath,
        dryRun: args.includes('--dry-run'),
    });
    const stats = await updater.run({
        recentPosts: optionValue(args, 'recent-posts', '100'),
        backfillPosts: optionValue(args, 'backfill-posts', '50'),
        backup: args.includes('--backup'),
    });
    console.log(`[catalogue] Complete ${JSON.stringify(stats)}`);
    if (stats.errors > 0) process.exitCode = 2;
}

main().catch(error => {
    console.error(`[catalogue] Fatal: ${error.stack || error.message}`);
    process.exitCode = 1;
});
