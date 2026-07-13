from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

import aiohttp
from bs4 import BeautifulSoup

from .library import Library
from .pcloud import PCloudClient, PCloudError, extract_code

FEED = "https://phyuniwarpyar.blogspot.com/feeds/posts/default"


class SiteScraper:
    def __init__(self, library: Library) -> None:
        self.library = library

    async def sync_posts(
        self,
        max_posts: int | None = 500,
        scan_pcloud: bool = True,
        incremental: bool = True,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        post_count = 0
        total_posts = 0
        links: list[tuple[str, str, str, str]] = []
        headers = {"User-Agent": "PhyuNiWarPyarDiscordBot/1.0 (authorized archival bot)"}
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            start = 1
            while max_posts is None or post_count < max_posts:
                page_size = 100 if max_posts is None else min(100, max_posts - post_count)
                params = {"alt": "json", "start-index": start, "max-results": page_size}
                async with session.get(FEED, params=params) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                entries = payload.get("feed", {}).get("entry", [])
                total_posts = int(payload.get("feed", {}).get("openSearch$totalResults", {}).get("$t", 0))
                if not entries:
                    break
                entry_urls = [
                    next((x["href"] for x in entry.get("link", []) if x.get("rel") == "alternate"), "")
                    for entry in entries
                ]
                known_updates = await self.library.source_updates(entry_urls) if incremental else {}
                posts: list[dict[str, str]] = []
                for entry, url in zip(entries, entry_urls):
                    html = entry.get("content", entry.get("summary", {})).get("$t", "")
                    soup = BeautifulSoup(html, "html.parser")
                    title = entry.get("title", {}).get("$t", "Untitled")
                    cover = soup.select_one("img[src]")
                    source_updated = entry.get("updated", {}).get("$t", "")
                    if not incremental or known_updates.get(url) != source_updated:
                        for anchor in soup.select("a[href]"):
                            share_url = anchor.get("href", "")
                            code = extract_code(share_url)
                            if code:
                                links.append((url, title, share_url, code))
                    posts.append({
                        "url": url,
                        "title": title,
                        "published": entry.get("published", {}).get("$t", ""),
                        "content": soup.get_text(" ", strip=True),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "source_updated": source_updated,
                        "cover_url": cover.get("src", "") if cover else "",
                    })
                await self.library.upsert_posts(posts)
                post_count += len(posts)
                if progress:
                    progress(post_count, total_posts)
                start += len(entries)
                if len(entries) < page_size:
                    break
            track_count = 0
            failed = 0
            if scan_pcloud:
                client = PCloudClient(session)
                for post_url, post_title, share_url, code in dict.fromkeys(links):
                    now = datetime.now(timezone.utc).isoformat()
                    try:
                        album_title, tracks = await client.list_tracks(code)
                        track_count += await self.library.upsert_pcloud_album(
                            post_url, code, share_url, album_title or post_title, now,
                            [track.__dict__ for track in tracks],
                        )
                    except (PCloudError, aiohttp.ClientError) as exc:
                        failed += 1
                        await self.library.upsert_pcloud_album(
                            post_url, code, share_url, post_title, now, [], str(exc)
                        )
                    await asyncio.sleep(0.25)
            else:
                await self.library.upsert_pending_pcloud_links(
                    links, datetime.now(timezone.utc).isoformat()
                )
        return {"posts": post_count, "albums": len(set(x[3] for x in links)), "tracks": track_count, "failed": failed}
