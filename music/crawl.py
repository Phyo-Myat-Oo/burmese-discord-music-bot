from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .library import Library
from .scraper import SiteScraper


async def run(max_posts: int | None, posts_only: bool) -> None:
    library = Library(Path("data/music.db"), Path("data/music"))
    await library.initialize()
    def progress(done: int, total: int) -> None:
        print(f"Posts: {done}/{total}", flush=True)

    result = await SiteScraper(library).sync_posts(
        max_posts=max_posts, scan_pcloud=not posts_only, progress=progress
    )
    print(
        f"Indexed {result['posts']} posts, found {result['albums']} pCloud albums, "
        f"saved {result['tracks']} tracks, failures: {result['failed']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Index Blogspot pCloud music metadata")
    parser.add_argument("--max-posts", type=int, default=100)
    parser.add_argument("--all", action="store_true", help="Index every Blogspot post")
    parser.add_argument("--posts-only", action="store_true", help="Skip pCloud folder scanning")
    args = parser.parse_args()
    asyncio.run(run(None if args.all else args.max_posts, args.posts_only))


if __name__ == "__main__":
    main()
