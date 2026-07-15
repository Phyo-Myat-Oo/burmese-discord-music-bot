from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .catalogue import Catalogue
from .config import load_settings
from .text import clean_title, search_key


FEED_URL = "https://phyuniwarpyar.blogspot.com/feeds/posts/default"
PCLOUD_HOSTS = ("https://api.pcloud.com", "https://eapi.pcloud.com")
MEDIAFIRE_API = "https://www.mediafire.com/api/1.4"
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav")


class IndexError(RuntimeError):
    pass


class MediaFireIndexError(IndexError):
    pass


def pcloud_code(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.casefold().endswith("pcloud.link"):
        return None
    return parse_qs(parsed.query).get("code", [None])[0]


def mediafire_folder_key(url: str) -> str | None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    if not hostname.endswith("mediafire.com"):
        return None
    match = re.search(r"/folder/([A-Za-z0-9]+)", parsed.path)
    return match.group(1) if match else None


def mediafire_folder_url(url: str) -> str | None:
    return url if mediafire_folder_key(url) else None


async def mediafire_tracks(session: aiohttp.ClientSession, url: str) -> list[dict]:
    folder_key = mediafire_folder_key(url)
    if not folder_key:
        raise MediaFireIndexError("MediaFire URL is not a folder link")
    tracks: list[dict] = []
    chunk = 1
    while True:
        try:
            async with session.get(
                f"{MEDIAFIRE_API}/folder/get_content.php",
                params={
                    "folder_key": folder_key,
                    "content_type": "files",
                    "filter": "all",
                    "chunk": chunk,
                },
            ) as response:
                response.raise_for_status()
                root = ET.fromstring(await response.read())
        except (aiohttp.ClientError, asyncio.TimeoutError, ET.ParseError) as exc:
            raise MediaFireIndexError(f"MediaFire folder lookup failed: {exc}") from exc
        error = root.findtext(".//error")
        if error and error.strip() not in {"0", ""}:
            raise MediaFireIndexError(error.strip())
        for item in root.findall(".//files/file"):
            filename = (item.findtext("filename") or "").strip()
            mimetype = (item.findtext("mimetype") or "").strip()
            quick_key = (item.findtext("quickkey") or "").strip()
            if not quick_key or not filename:
                continue
            if not (mimetype.startswith("audio/") or filename.casefold().endswith(AUDIO_EXTENSIONS)):
                continue
            raw_size = (item.findtext("size") or "0").strip()
            try:
                size = int(raw_size)
            except ValueError:
                size = 0
            tracks.append({
                "mediafire_quick_key": quick_key,
                "title": filename,
                "size": size,
                "content_type": mimetype,
                "duration": None,
                "artist": "",
            })
        if (root.findtext(".//more_chunks") or "no").strip().casefold() != "yes":
            break
        chunk += 1
    if not tracks:
        raise MediaFireIndexError("MediaFire folder contains no audio files")
    return tracks


def attach_mediafire_matches(pcloud: list[dict], mediafire: list[dict]) -> None:
    """Attach stable MediaFire quick keys by normalized filename."""
    available: dict[str, dict] = {}
    for track in mediafire:
        key = search_key(clean_title(str(track.get("title") or "")))
        if key and key not in available:
            available[key] = track
    for track in pcloud:
        key = search_key(clean_title(str(track.get("title") or "")))
        match = available.get(key)
        if match:
            track["mediafire_quick_key"] = match["mediafire_quick_key"]


async def pcloud_payload(session: aiohttp.ClientSession, method: str, params: dict[str, str | int]) -> dict:
    last_error = "pCloud request failed"
    for host in PCLOUD_HOSTS:
        async with session.get(f"{host}/{method}", params=params) as response:
            response.raise_for_status()
            payload = json.loads((await response.read()).decode("utf-8", errors="replace"))
        if payload.get("result") == 0:
            return payload
        last_error = str(payload.get("error") or last_error)
        if payload.get("result") != 7001:
            break
    raise IndexError(last_error)


async def pcloud_tracks(session: aiohttp.ClientSession, code: str) -> tuple[str, list[dict]]:
    metadata = (await pcloud_payload(session, "showpublink", {"code": code}))["metadata"]
    tracks: list[dict] = []

    def walk(item: dict) -> None:
        for child in item.get("contents", []):
            if child.get("isfolder"):
                walk(child)
                continue
            name = child.get("name", "Untitled")
            content_type = child.get("contenttype", "")
            if content_type.startswith("audio/") or name.casefold().endswith(AUDIO_EXTENSIONS):
                tracks.append({
                    "file_id": int(child["fileid"]), "title": name, "size": int(child.get("size", 0)),
                    "content_type": content_type, "duration": child.get("duration"),
                    "artist": (child.get("artist") or "").strip(),
                })

    walk(metadata if metadata.get("isfolder") else {"contents": [metadata]})
    return metadata.get("name", "pCloud album"), tracks


@dataclass(frozen=True)
class SyncResult:
    posts: int = 0
    albums: int = 0
    tracks: int = 0
    failed: int = 0


class BlogspotIndexer:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    async def sync(
        self, *, max_posts: int | None = 100, scan_pcloud: bool = True, incremental: bool = True,
        progress: Callable[[int, int], None] | None = None,
        album_progress: Callable[[int, int, int, int], None] | None = None,
    ) -> SyncResult:
        result = SyncResult()
        post_count = 0
        candidate_links: dict[str, tuple[str, str, str]] = {}
        mediafire_by_post: dict[str, str] = {}
        post_titles: dict[str, str] = {}
        changed_codes: set[str] = set()
        headers = {"User-Agent": "DaisyV2/0.1 (authorized catalogue indexer)"}
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            start = 1
            total_posts = 0
            while max_posts is None or post_count < max_posts:
                page_size = 100 if max_posts is None else min(100, max_posts - post_count)
                async with session.get(FEED_URL, params={"alt": "json", "start-index": start, "max-results": page_size}) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                feed = payload.get("feed", {})
                entries = feed.get("entry", [])
                total_posts = int(feed.get("openSearch$totalResults", {}).get("$t", 0))
                if not entries:
                    break
                entry_urls = [
                    next((link["href"] for link in entry.get("link", []) if link.get("rel") == "alternate"), "")
                    for entry in entries
                ]
                updates = await self.catalogue.source_updates(entry_urls) if incremental else {}
                posts: list[dict[str, str]] = []
                for entry, post_url in zip(entries, entry_urls):
                    html = entry.get("content", entry.get("summary", {})).get("$t", "")
                    soup = BeautifulSoup(html, "html.parser")
                    title = entry.get("title", {}).get("$t", "Untitled")
                    source_updated = entry.get("updated", {}).get("$t", "")
                    image = soup.select_one("img[src]")
                    posts.append({
                        "url": post_url, "title": title, "published": entry.get("published", {}).get("$t", ""),
                        "source_updated": source_updated, "cover_url": image.get("src", "") if image else "",
                    })
                    post_titles[post_url] = title
                    changed = not incremental or updates.get(post_url) != source_updated
                    for anchor in soup.select("a[href]"):
                        share_url = anchor.get("href", "")
                        code = pcloud_code(share_url)
                        if code:
                            candidate_links[code] = (post_url, title, share_url)
                            if changed:
                                changed_codes.add(code)
                        mediafire_url = mediafire_folder_url(share_url)
                        if mediafire_url:
                            mediafire_by_post[post_url] = mediafire_url
                await self.catalogue.upsert_posts(posts)
                post_count += len(posts)
                if progress:
                    progress(post_count, total_posts)
                start += len(entries)
                if len(entries) < page_size:
                    break

            links: dict[str, tuple[str, str, str]] = {}
            if scan_pcloud:
                if incremental:
                    retry_codes = await self.catalogue.pcloud_scan_needed(list(candidate_links))
                    scan_codes = changed_codes | retry_codes
                else:
                    scan_codes = set(candidate_links)
                links = {code: candidate_links[code] for code in scan_codes}

            pcloud_posts = {item[0] for item in candidate_links.values()}
            mediafire_only = [
                (post_url, mediafire_url)
                for post_url, mediafire_url in mediafire_by_post.items()
                if post_url not in pcloud_posts
            ]
            total_album_scans = len(links) + len(mediafire_only)
            completed_album_scans = 0
            saved_tracks = failed = 0
            if scan_pcloud:
                for code, (post_url, fallback_title, share_url) in links.items():
                    now = datetime.now(timezone.utc).isoformat()
                    mediafire_url = mediafire_by_post.get(post_url)
                    try:
                        title, tracks = await pcloud_tracks(session, code)
                        if mediafire_url:
                            try:
                                alternate_tracks = await mediafire_tracks(session, mediafire_url)
                                attach_mediafire_matches(tracks, alternate_tracks)
                            except (MediaFireIndexError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                                # pCloud remains playable; MediaFire is only an optional fallback.
                                # Keep the folder URL so a later refresh can try again.
                                logging.getLogger(__name__).warning(
                                    "MediaFire indexing failed for %s: %s", mediafire_url, exc
                                )
                        saved_tracks += await self.catalogue.upsert_album(
                            post_url=post_url, code=code, share_url=share_url,
                            title=title or fallback_title, scanned_at=now, tracks=tracks,
                            mediafire_url=mediafire_url,
                        )
                    except (IndexError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        failed += 1
                        await self.catalogue.upsert_album(
                            post_url=post_url, code=code, share_url=share_url,
                            title=fallback_title, scanned_at=now, tracks=None, error=str(exc),
                            mediafire_url=mediafire_url,
                        )
                    completed_album_scans += 1
                    if album_progress:
                        album_progress(completed_album_scans, total_album_scans, saved_tracks, failed)
                    await asyncio.sleep(0.2)
            mediafire_only_albums = 0
            if scan_pcloud:
                for post_url, mediafire_url in mediafire_only:
                    try:
                        alternate_tracks = await mediafire_tracks(session, mediafire_url)
                        folder_key = mediafire_folder_key(mediafire_url)
                        if not folder_key:
                            continue
                        tracks = []
                        for item in alternate_tracks:
                            quick_key = item["mediafire_quick_key"]
                            stable_id = int(hashlib.sha256(quick_key.encode("utf-8")).hexdigest()[:15], 16)
                            tracks.append({**item, "file_id": stable_id})
                        code = f"mediafire-{folder_key}"
                        now = datetime.now(timezone.utc).isoformat()
                        saved_tracks += await self.catalogue.upsert_album(
                            post_url=post_url, code=code, share_url=mediafire_url,
                            title=post_titles.get(post_url, "MediaFire album"), scanned_at=now,
                            tracks=tracks, mediafire_url=mediafire_url,
                        )
                        mediafire_only_albums += 1
                    except (MediaFireIndexError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        failed += 1
                        logging.getLogger(__name__).warning(
                            "MediaFire-only indexing failed for %s: %s", mediafire_url, exc
                        )
                    completed_album_scans += 1
                    if album_progress:
                        album_progress(completed_album_scans, total_album_scans, saved_tracks, failed)

        return SyncResult(post_count, len(links) + mediafire_only_albums, saved_tracks, failed)


async def _run_cli(max_posts: int | None, posts_only: bool, refresh: bool) -> None:
    settings = load_settings()
    catalogue = Catalogue(settings.catalog_db)
    await catalogue.initialize()
    indexer = BlogspotIndexer(catalogue)

    def show_album_progress(done: int, total: int, tracks: int, failed: int) -> None:
        if done == 1 or done % 10 == 0 or done == total:
            print(
                f"Albums: {done}/{total} | Tracks saved: {tracks} | Failed: {failed}",
                flush=True,
            )

    result = await indexer.sync(
        max_posts=max_posts, scan_pcloud=not posts_only, incremental=not refresh,
        progress=lambda done, total: print(f"Posts: {done}/{total}", flush=True),
        album_progress=show_album_progress,
    )
    print(f"Indexed {result.posts} posts, {result.albums} albums, {result.tracks} tracks; {result.failed} failed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Index authorized Blogspot/pCloud catalogue metadata for Daisy V2")
    parser.add_argument("--max-posts", type=int, default=100)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--posts-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run_cli(None if args.all else args.max_posts, args.posts_only, args.refresh))


if __name__ == "__main__":
    main()
