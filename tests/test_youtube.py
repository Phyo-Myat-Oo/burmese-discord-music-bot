import unittest

from music.youtube import YouTubeResult, is_youtube_url


class YouTubeTests(unittest.TestCase):
    def test_accepts_only_youtube_hosts(self):
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(is_youtube_url("https://youtu.be/abc"))
        self.assertFalse(is_youtube_url("https://example.com/watch?v=abc"))

    def test_formats_duration(self):
        result = YouTubeResult("Song", "https://youtu.be/abc", "Artist", 243)
        self.assertEqual(result.duration_text, "4:03")


if __name__ == "__main__":
    unittest.main()
