from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from daisy_v2.catalogue import Catalogue
from daisy_v2.models import QueueTrack, SourceType
from daisy_v2.state import UserState


class V2CatalogueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.catalogue_path = root / "v2" / "catalog.db"
        self.state_path = root / "v2" / "state.db"
        self.catalogue = Catalogue(self.catalogue_path)
        await self.catalogue.initialize()
        await self.catalogue.upsert_posts([{
            "url": "https://example.test/post", "title": "Artist - Album", "published": "now",
            "source_updated": "now", "cover_url": "https://example.test/cover.jpg",
        }])
        self.saved_tracks = await self.catalogue.upsert_album(
            post_url="https://example.test/post", code="safe-code", share_url="https://u.pcloud.link/test",
            title="Artist - Album", scanned_at="now", tracks=[{
                "file_id": 123, "title": "01. Burmese Song.mp3", "size": 100,
                "content_type": "audio/mpeg", "duration": 245, "artist": "Artist",
            }],
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_catalogue_preserves_stable_pcloud_identity(self) -> None:
        self.assertEqual(self.saved_tracks, 1)
        tracks = await self.catalogue.search_tracks("Burmese")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].pcloud_code, "safe-code")
        self.assertEqual(tracks[0].pcloud_file_id, 123)
        self.assertEqual(tracks[0].cache_key, "pcloud:safe-code:123")

    async def test_catalogue_keeps_public_tracks_separate_from_private_state(self) -> None:
        with closing(sqlite3.connect(self.catalogue_path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("favorites", tables)
        state = UserState(self.state_path)
        await state.initialize()
        item = QueueTrack(
            source_type=SourceType.PCLOUD, source_key="pcloud:safe-code:123", title="Burmese Song",
            artist="Artist", album="Album", requester_id=1, requester_name="Tester",
        )
        self.assertTrue(await state.add_favorite(1, item))
        self.assertEqual((await state.list_favorites(1))[0]["title"], "Burmese Song")

    async def test_artist_and_album_queries_use_catalogue_data(self) -> None:
        artists = await self.catalogue.artists("Artist")
        self.assertEqual(artists[0].name, "Artist")
        albums = await self.catalogue.albums_for_artist(artists[0].id)
        self.assertEqual(albums[0].title, "Artist - Album")
        tracks = await self.catalogue.album_tracks(albums[0].id)
        self.assertEqual(tracks[0].title, "Burmese Song")

    async def test_private_playlists_and_history_are_persisted_separately(self) -> None:
        state = UserState(self.state_path)
        await state.initialize()
        item = QueueTrack(
            source_type=SourceType.PCLOUD, source_key="pcloud:safe-code:123", title="Burmese Song",
            artist="Artist", album="Album", requester_id=7, requester_name="Tester", duration=245,
            pcloud_code="safe-code", pcloud_file_id=123,
        )
        self.assertTrue(await state.create_playlist("personal", "My mix", 7, 10))
        self.assertTrue(await state.add_playlist_item("personal", "My mix", 7, 10, item))
        self.assertEqual((await state.playlist_items("personal", "My mix", 7, 10))[0]["title"], "Burmese Song")
        self.assertTrue(await state.remove_playlist_item("personal", "My mix", 1, 7, 10))
        history_id = await state.record_start(10, item)
        await state.complete(history_id)
        self.assertEqual((await state.history(10, 7))[0]["completed"], 1)
        self.assertEqual((await state.top(10, "title"))[0]["plays"], 1)
        self.assertEqual(await state.stats(10, 7), {"plays": 1, "seconds": 245})

    async def test_v2_index_writes_normalized_album_and_track_data(self) -> None:
        await self.catalogue.upsert_posts([{
            "url": "https://example.test/new", "title": "New Artist - New Album", "published": "now",
            "source_updated": "now", "cover_url": "https://example.test/new.jpg",
        }])
        saved = await self.catalogue.upsert_album(
            post_url="https://example.test/new", code="new-code", share_url="https://u.pcloud.link/new",
            title="New Artist - New Album", scanned_at="now", tracks=[{
                "file_id": 88, "title": "02. New Song.mp3", "size": 200,
                "content_type": "audio/mpeg", "duration": 120, "artist": "New Artist",
            }],
        )
        self.assertEqual(saved, 1)
        found = await self.catalogue.search_tracks("New Song")
        self.assertEqual(found[0].pcloud_code, "new-code")
        self.assertEqual(found[0].title, "New Song")
        album = await self.catalogue.random_album("New Artist")
        self.assertIsNotNone(album)
        self.assertEqual(album.title, "New Artist - New Album")

if __name__ == "__main__":
    unittest.main()
