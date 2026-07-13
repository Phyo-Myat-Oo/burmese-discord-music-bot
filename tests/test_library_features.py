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
        await self.library.upsert_posts([{
            "url": "https://example.test/post", "title": "Album post",
            "published": "now", "content": "", "updated_at": "now",
            "source_updated": "now", "cover_url": "https://example.test/cover.jpg",
        }])
        await self.library.upsert_pcloud_album(
            "https://example.test/post", "code", "https://u.pcloud.link/test",
            "မာမာအေး - စမ်းသပ်အခွေ", "now",
            [{
                "file_id": 1, "title": "၀၁. စမ်းသပ်သီချင်း.mp3",
                "size": 100, "content_type": "audio/mpeg", "duration": 240,
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

    async def test_normalized_artist_relationship(self):
        artists = await self.library.list_artists("မာမာ", 10, 0)
        self.assertEqual(artists[0]["artist"], "မာမာအေး")

    async def test_album_lookup_and_suggestions_are_playable(self):
        album = await self.library.random_album()
        found = await self.library.find_pcloud_album(album["title"])
        suggestions = await self.library.autocomplete_albums(album["title"])
        self.assertEqual(found["id"], album["id"])
        self.assertEqual(suggestions[0]["id"], album["id"])
        self.assertEqual(await self.library.count_albums_by_artist("မာမာအေး"), 1)
        track = await self.library.random_track("မာမာအေး")
        self.assertEqual(track["duration"], 240)
        self.assertEqual(track["cover_url"], "https://example.test/cover.jpg")

    async def test_personal_playlist_and_history(self):
        track = await self.library.random_track("မာမာအေး")
        self.assertTrue(await self.library.create_playlist("personal", "ကြိုက်", 7, 9))
        self.assertFalse(await self.library.create_playlist("personal", "ကြိုက်", 7, 9))
        item = {
            "source_type": "pcloud", "track_id": track["id"],
            "title": track["title"], "artist": track["artist"],
            "album": track["album_title"], "duration": track["duration"],
        }
        self.assertTrue(await self.library.add_playlist_item("personal", "ကြိုက်", 7, 9, item))
        self.assertEqual(len(await self.library.playlist_items("personal", "ကြိုက်", 7, 9)), 1)
        history_id = await self.library.record_play_start(9, 7, item)
        await self.library.complete_history(history_id)
        stats = await self.library.user_stats(9, 7)
        self.assertEqual(stats["plays"], 1)
        self.assertEqual(stats["seconds"], 240)
        self.assertTrue(await self.library.remove_playlist_item("personal", "ကြိုက်", 1, 7, 9))


if __name__ == "__main__":
    unittest.main()
