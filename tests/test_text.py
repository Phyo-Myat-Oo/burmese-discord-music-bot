import unittest

from music.text import clean_album_title, clean_track_title, extract_artists, search_key


class BurmeseTextTests(unittest.TestCase):
    def test_cleans_track_number_and_extension(self):
        self.assertEqual(clean_track_title("၀၁.  အမေ့အိမ်.mp3"), "အမေ့အိမ်")

    def test_cleans_album_bitrate_suffix(self):
        self.assertEqual(clean_album_title("မာမာအေး - တေးများ (320-UNI)"), "မာမာအေး - တေးများ")

    def test_search_ignores_spacing_punctuation_and_digit_style(self):
        self.assertEqual(search_key("၀၁။ မာ မာ-အေး"), search_key("01 မာမာအေး"))

    def test_extracts_collaborating_artists(self):
        self.assertEqual(
            extract_artists("မာမာအေး၊ ချိုပြုံး - စုံတွဲတေးများ (320-UNI)"),
            ["မာမာအေး", "ချိုပြုံး"],
        )


if __name__ == "__main__":
    unittest.main()
