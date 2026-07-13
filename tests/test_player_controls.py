import unittest
from pathlib import Path
from unittest.mock import patch

from music.player import GuildPlayer, QueueItem


class FakeVoice:
    def __init__(self) -> None:
        self.stopped = False

    def is_playing(self) -> bool:
        return True

    def is_paused(self) -> bool:
        return False

    def stop(self) -> None:
        self.stopped = True


def item(title: str, history_id: int | None = None) -> QueueItem:
    return QueueItem(Path(title), title, history_id=history_id)


class PlayerControlTests(unittest.TestCase):
    def setUp(self) -> None:
        with patch("music.player.find_ffmpeg", return_value="ffmpeg"):
            self.player = GuildPlayer(None)  # type: ignore[arg-type]

    def test_repeat_mode_cycles_through_every_option(self):
        self.assertEqual(self.player.cycle_repeat_mode(), "track")
        self.assertEqual(self.player.cycle_repeat_mode(), "queue")
        self.assertEqual(self.player.cycle_repeat_mode(), "off")

    def test_back_requeues_previous_before_current_track(self):
        previous = item("Previous", history_id=10)
        current = item("Current", history_id=11)
        self.player.history = [previous]
        self.player.current = current
        self.player.voice = FakeVoice()  # type: ignore[assignment]

        self.assertTrue(self.player.previous())
        self.assertTrue(self.player._was_skipped)
        self.assertTrue(self.player.voice.stopped)
        self.assertEqual([entry.title for entry in self.player.queue._queue], ["Previous", "Current"])
        self.assertIsNone(self.player.queue._queue[0].history_id)
        self.assertIsNone(self.player.queue._queue[1].history_id)

    def test_stop_turns_repeat_off(self):
        self.player.set_repeat_mode("queue")
        self.player.stop()
        self.assertEqual(self.player.repeat_mode, "off")

    def test_clear_queue_preserves_current_track_and_turns_repeat_off(self):
        self.player.current = item("Current")
        self.player.queue.put_nowait(item("Next"))
        self.player.queue.put_nowait(item("Later"))
        self.player.set_repeat_mode("queue")

        self.assertEqual(self.player.clear_queue(), 2)
        self.assertEqual(self.player.queue.qsize(), 0)
        self.assertEqual(self.player.current.title, "Current")
        self.assertEqual(self.player.repeat_mode, "off")


if __name__ == "__main__":
    unittest.main()
