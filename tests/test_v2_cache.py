from __future__ import annotations

import asyncio
import tempfile
import unittest
import wave
from pathlib import Path

from daisy_v2.cache import DiskCache
from daisy_v2.models import QueueTrack, SourceType


class FakeSources:
    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def stable_file_name(source_key: str) -> str:
        return source_key.replace(":", "_") + ".media"

    async def download(self, track: QueueTrack, destination: Path, max_bytes: int) -> None:
        self.calls += 1
        await asyncio.sleep(0.02)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(b"\0\0" * 960)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.sources = FakeSources()
        self.cache = DiskCache(
            Path(self.temp.name), self.sources, max_bytes=1_000_000, max_track_bytes=100_000,
            ttl_seconds=3600, ffmpeg_path="not-a-real-ffmpeg",
        )
        await self.cache.initialize()
        self.track = QueueTrack(
            source_type=SourceType.PCLOUD, source_key="pcloud:code:1", title="Song", artist="Artist",
            album="Album", requester_id=1, requester_name="Tester", pcloud_code="code", pcloud_file_id=1,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_concurrent_requests_share_one_validated_download(self) -> None:
        first, second = await asyncio.gather(self.cache.ensure(self.track), self.cache.ensure(self.track))
        self.assertEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertEqual(self.sources.calls, 1)
        third = await self.cache.ensure(self.track)
        self.assertEqual(third, first)
        self.assertEqual(self.sources.calls, 1)


if __name__ == "__main__":
    unittest.main()
