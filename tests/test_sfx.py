"""Unit tests for MyInstants sounds and SFX mixing."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from studio import config, jobs, render, sfx


class TestSfx(unittest.TestCase):
    def test_curated_search(self):
        # Searching for 'vine boom' should find Vine Boom
        res = sfx.search("vine boom", limit=5)
        self.assertTrue(len(res) > 0)
        names = [r["name"].lower() for r in res]
        self.assertTrue(any("vine boom" in n for n in names))

    def test_search_bruh(self):
        res = sfx.search("bruh", limit=5)
        self.assertTrue(len(res) > 0)
        urls = [r["url"] for r in res]
        self.assertTrue(any("movie_1.mp3" in u or "bruh" in u for u in urls))

    def test_ensure_cached(self):
        # Test caching of Vine Boom sound
        url = "https://www.myinstants.com/media/sounds/vine-boom.mp3"
        cached_path = sfx.ensure_cached(url, "vine-boom")
        self.assertIsNotNone(cached_path)
        self.assertTrue(cached_path.exists())
        self.assertGreater(cached_path.stat().st_size, 1000)

    def test_suggest_for_clip(self):
        clip = {
            "start": 10.0,
            "end": 35.0,
            "category": "funny",
            "hook": "UNBELIEVABLE SHOT",
            "title": "IShowSpeed Epic GTA 6 Moment 💀",
            "highlight_words": ["shot", "epic"],
        }
        words = [
            {"w": "look", "s": 12.0, "e": 12.5},
            {"w": "at", "s": 12.6, "e": 12.8},
            {"w": "that", "s": 12.9, "e": 13.2},
            {"w": "shot", "s": 25.0, "e": 25.5},
        ]
        sug = sfx.suggest_for_clip(clip, words)
        self.assertTrue(len(sug) >= 1)
        # Should have valid fields
        for s in sug:
            self.assertIn("name", s)
            self.assertIn("url", s)
            self.assertIn("time", s)
            self.assertIn("vol", s)
            self.assertGreaterEqual(s["time"], 0.0)
            self.assertLessEqual(s["time"], 25.0)

    def test_render_with_sfx_ffmpeg(self):
        # Create a small 3-second test clip and verify rendering with SFX
        work_dir = Path(tempfile.mkdtemp(prefix="pk_test_sfx"))
        try:
            # Generate a 3s test video with ffmpeg
            src_video = work_dir / "src.mp4"
            render.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc=duration=3:size=1280x720:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-c:a", "aac", str(src_video)
            ])
            self.assertTrue(src_video.exists())

            # Prepare clip with SFX
            clip = {
                "start": 0.0,
                "end": 3.0,
                "layout": "crop",
                "captions": False,
                "hook": "TEST HOOK",
                "sfx": [
                    {
                        "name": "Vine Boom",
                        "url": "https://www.myinstants.com/media/sounds/vine-boom.mp3",
                        "time": 0.5,
                        "vol": 0.7,
                    }
                ]
            }
            out = render.render_clip(src_video, work_dir / "out", "clip_test", clip, [], 1280, 720)
            self.assertTrue((work_dir / "out" / "clip_test.mp4").exists())
            self.assertGreater((work_dir / "out" / "clip_test.mp4").stat().st_size, 10000)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
