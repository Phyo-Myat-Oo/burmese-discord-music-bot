from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

API_HOSTS = ("https://api.pcloud.com", "https://eapi.pcloud.com")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav")


class PCloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class PCloudTrack:
    file_id: int
    title: str
    size: int
    content_type: str


def extract_code(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.lower().endswith("pcloud.link"):
        return None
    query = parse_qs(parsed.query)
    return query.get("code", [None])[0]


class PCloudClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def _call(self, method: str, params: dict[str, str | int]) -> dict:
        last_error = "pCloud API request failed"
        for host in API_HOSTS:
            async with self.session.get(f"{host}/{method}", params=params) as response:
                response.raise_for_status()
                # Some legacy Burmese filenames contain invalid UTF-8 bytes.
                # Replacement keeps the rest of the folder metadata indexable.
                raw = await response.read()
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            if payload.get("result") == 0:
                return payload
            last_error = payload.get("error", last_error)
            if payload.get("result") != 7001:
                break
        raise PCloudError(last_error)

    async def list_tracks(self, code: str) -> tuple[str, list[PCloudTrack]]:
        metadata = (await self._call("showpublink", {"code": code}))["metadata"]
        tracks: list[PCloudTrack] = []

        def walk(item: dict) -> None:
            for child in item.get("contents", []):
                if child.get("isfolder"):
                    walk(child)
                    continue
                name = child.get("name", "Untitled")
                content_type = child.get("contenttype", "")
                if content_type.startswith("audio/") or name.lower().endswith(AUDIO_EXTENSIONS):
                    tracks.append(PCloudTrack(
                        file_id=int(child["fileid"]), title=name,
                        size=int(child.get("size", 0)), content_type=content_type,
                    ))

        walk(metadata if metadata.get("isfolder") else {"contents": [metadata]})
        return metadata.get("name", "pCloud album"), tracks

    async def stream_url(self, code: str, file_id: int) -> str:
        payload = await self._call(
            "getpublinkdownload", {"code": code, "fileid": file_id, "forcedownload": 0}
        )
        hosts, path = payload.get("hosts", []), payload.get("path")
        if not hosts or not path:
            raise PCloudError("pCloud returned no playback host")
        return f"https://{hosts[0]}{quote(path, safe='/:%@?&=+$,;~()*!')}"
