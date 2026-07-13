import tempfile
import unittest
from pathlib import Path

from music.library import Library


class LibraryFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.library = Library(root / "music.db", root / "music")
        await self.library.initialize()
        await self.library.upsert_pcloud_album(
            "https://example.test/post", "code", "https://u.pcloud.link/test",
            "မာမာအေး - စမ်းသပ်အခွေ", "now",
            [{
                "file_id": 1, "title": "၀၁. စမ်းသပ်သီချင်း.mp3",
                "size": 100, "content_type": "audio/mpeg",
            }],
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_favorites_are_per_user_and_playable(self):
        track = await self.library.random_track("မာမာအေး")
        self.assertIsNotNone(track)
        self.assertTrue(await self.library.add_favorite(123, track["id"]))
        self.assertFalse(await self.library.add_favorite(123, track["id"]))
        rows = await self.library.list_favorites(123)
        self.assertEqual(rows[0]["title"], "စမ်းသပ်သီချင်း")
        self.assertTrue(await self.library.remove_favorite(123, track["id"]))

    async def test_random_album_returns_its_tracks(self):
        album = await self.library.random_album("မာမာအေး")
        tracks = await self.library.album_playback_tracks(album["id"])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["title"], "စမ်းသပ်သီချင်း")

    async def test_youtube_and_pcloud_favorites_share_one_list(self):
        track = await self.library.random_track("မာမာအေး")
        await self.library.add_favorite(456, track["id"])
        added = await self.library.add_youtube_favorite(
            456, "https://youtu.be/example", "YouTube Song", "Channel", 240, None
        )
        self.assertTrue(added)
        self.assertEqual(await self.library.count_favorites(456), 2)
        rows = await self.library.list_favorites(456)
        self.assertEqual({row["source_type"] for row in rows}, {"pcloud", "youtube"})
        self.assertTrue(
            await self.library.remove_youtube_favorite(456, "https://youtu.be/example")
        )


if __name__ == "__main__":
    unittest.main()
