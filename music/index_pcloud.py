from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from .library import Library
from .pcloud import PCloudClient, PCloudError


async def index_pending(
    concurrency: int, limit: int | None, retry_failed: bool, refresh_all: bool,
    refresh_missing: bool,
) -> None:
    library = Library(Path("data/music.db"), Path("data/music"))
    await library.initialize()
    if refresh_all:
        albums = await library.all_pcloud_albums(limit)
    elif refresh_missing:
        albums = await library.pcloud_albums_needing_refresh(limit)
    else:
        albums = await library.pending_pcloud_albums(retry_failed, limit)
    total = len(albums)
    if not total:
        print("No pending pCloud albums.")
        return

    queue: asyncio.Queue[dict] = asyncio.Queue()
    for album in albums:
        queue.put_nowait(album)

    completed = successful = failed = tracks_saved = 0
    stats_lock = asyncio.Lock()
    database_lock = asyncio.Lock()
    timeout = aiohttp.ClientTimeout(total=45)
    connector = aiohttp.TCPConnector(limit=concurrency * 2)
    headers = {"User-Agent": "PhyuNiWarPyarDiscordBot/1.0 (authorized archival bot)"}

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
        client = PCloudClient(session)

        async def worker() -> None:
            nonlocal completed, successful, failed, tracks_saved
            while True:
                try:
                    album = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                error: str | None = None
                title = album["title"]
                tracks = []
                for attempt in range(3):
                    try:
                        title, found = await client.list_tracks(album["code"])
                        tracks = [track.__dict__ for track in found]
                        error = None
                        break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        error = str(exc) or type(exc).__name__
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                    except PCloudError as exc:
                        error = str(exc)
                        break

                async with database_lock:
                    count = await library.upsert_pcloud_album(
                        album["post_url"], album["code"], album["share_url"], title,
                        datetime.now(timezone.utc).isoformat(), tracks, error,
                    )
                async with stats_lock:
                    completed += 1
                    if error is None:
                        successful += 1
                        tracks_saved += count
                    else:
                        failed += 1
                    if completed % 25 == 0 or completed == total:
                        print(
                            f"Albums: {completed}/{total} | successful: {successful} | "
                            f"failed: {failed} | tracks: {tracks_saved}", flush=True
                        )
                queue.task_done()

        await asyncio.gather(*(worker() for _ in range(concurrency)))

    print(
        f"Finished {completed} albums: {successful} successful, {failed} failed, "
        f"{tracks_saved} tracks saved."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Index pending pCloud album folders")
    parser.add_argument("--concurrency", type=int, default=6, choices=range(1, 13))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--refresh-all", action="store_true", help="Refresh all album metadata")
    parser.add_argument("--refresh-missing", action="store_true", help="Resume metadata refresh")
    args = parser.parse_args()
    asyncio.run(index_pending(
        args.concurrency, args.limit, args.retry_failed, args.refresh_all, args.refresh_missing
    ))


if __name__ == "__main__":
    main()
