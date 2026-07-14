import os
import unittest
from pathlib import Path
from unittest.mock import patch

from music.player import (
    BufferedOpusAudio,
    GuildPlayer,
    QueueItem,
    audio_output_mode,
    prebuffer_frame_count,
)


class FakeVoice:
    def __init__(self) -> None:
        self.stopped = False

    def is_playing(self) -> bool:
        return True

    def is_paused(self) -> bool:
        return False

    def stop(self) -> None:
        self.stopped = True


class FakeOpusSource:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = iter(frames)
        self.cleaned = False

    def read(self) -> bytes:
        return next(self.frames, b"")

    def is_opus(self) -> bool:
        return True

    def cleanup(self) -> None:
        self.cleaned = True


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

    def test_move_queue_item_reorders_upcoming_tracks_only(self):
        self.player.current = item("Current")
        self.player.queue.put_nowait(item("First"))
        self.player.queue.put_nowait(item("Second"))
        self.player.queue.put_nowait(item("Third"))

        self.assertEqual(self.player.move_queue_item(2, -1), 1)
        self.assertEqual(
            [entry.title for entry in self.player.upcoming(None)], ["First", "Third", "Second"]
        )
        self.assertIsNone(self.player.move_queue_item(0, -1))
        self.assertEqual(self.player.current.title, "Current")

    def test_audio_prebuffer_reads_frames_in_order(self):
        source = FakeOpusSource([b"one", b"two", b"three"])
        buffered = BufferedOpusAudio(source, frame_count=2)
        self.assertTrue(buffered.is_opus())
        self.assertEqual(buffered.read(), b"one")
        self.assertEqual(buffered.read(), b"two")
        self.assertEqual(buffered.read(), b"three")
        self.assertEqual(buffered.read(), b"")
        buffered.cleanup()
        self.assertTrue(source.cleaned)

    def test_prebuffer_configuration_is_bounded(self):
        with patch.dict(os.environ, {"AUDIO_PREBUFFER_SECONDS": "99"}):
            self.assertEqual(prebuffer_frame_count(), 250)
        with patch.dict(os.environ, {"AUDIO_PREBUFFER_SECONDS": "invalid"}):
            self.assertEqual(prebuffer_frame_count(), 100)

    def test_pcm_is_default_and_invalid_output_mode_falls_back_to_pcm(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(audio_output_mode(), "pcm")
        with patch.dict(os.environ, {"AUDIO_OUTPUT_MODE": "opus"}):
            self.assertEqual(audio_output_mode(), "opus")
        with patch.dict(os.environ, {"AUDIO_OUTPUT_MODE": "not-a-format"}):
            self.assertEqual(audio_output_mode(), "pcm")


if __name__ == "__main__":
    unittest.main()
