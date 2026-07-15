from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from daisy_v2.models import QueueTrack, SourceType
from daisy_v2.player import GuildPlayer


class SlowCache:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    def pin(self, _track) -> None:
        pass

    def unpin(self, _track) -> None:
        pass

    async def ensure(self, _track):
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def prefetch(self, _tracks) -> None:
        pass


class FakeVoice:
    def is_connected(self) -> bool:
        return True

    def is_playing(self) -> bool:
        return False

    def is_paused(self) -> bool:
        return False

    def stop(self) -> None:
        pass

    async def disconnect(self, *, force: bool) -> None:
        pass


class PlayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_during_buffering_advances_without_waiting_for_download(self) -> None:
        cache = SlowCache()
        with patch("daisy_v2.player.find_ffmpeg", return_value="ffmpeg"):
            player = GuildPlayer(1, cache, idle_seconds=300)  # type: ignore[arg-type]
        player.voice = FakeVoice()  # type: ignore[assignment]
        track = QueueTrack(
            source_type=SourceType.PCLOUD, source_key="pcloud:code:1", title="Song", artist="Artist",
            album="Album", requester_id=1, requester_name="Tester", pcloud_code="code", pcloud_file_id=1,
        )
        await player.enqueue([track])
        await asyncio.wait_for(cache.started.wait(), timeout=1)
        self.assertTrue(await player.skip())
        for _ in range(20):
            current, _, _ = await player.snapshot()
            if current is None:
                break
            await asyncio.sleep(0.01)
        current, _, _ = await player.snapshot()
        self.assertIsNone(current)
        await player.close()


if __name__ == "__main__":
    unittest.main()
