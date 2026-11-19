import unittest
from pathlib import Path
from studio import jobs, live, media


class TestLiveStream(unittest.TestCase):
    def test_clean_options_live(self):
        opts = jobs.clean_options({"live_minutes": 45, "watch_live": True, "hinglish": True})
        self.assertEqual(opts["live_minutes"], 45)
        self.assertTrue(opts["watch_live"])
        self.assertTrue(opts["hinglish"])

        # Clamping
        opts_clamped = jobs.clean_options({"live_minutes": 300, "watch_live": False})
        self.assertEqual(opts_clamped["live_minutes"], 240)
        self.assertFalse(opts_clamped["watch_live"])

        opts_min = jobs.clean_options({"live_minutes": 1})
        self.assertEqual(opts_min["live_minutes"], 5)

    def test_youtube_video_id(self):
        self.assertEqual(live._youtube_video_id("https://www.youtube.com/watch?v=jfKfPfyJRdk"), "jfKfPfyJRdk")
        self.assertEqual(live._youtube_video_id("https://www.youtube.com/live/jfKfPfyJRdk?feature=share"), "jfKfPfyJRdk")
        self.assertEqual(live._youtube_video_id("https://youtu.be/jfKfPfyJRdk"), "jfKfPfyJRdk")
        self.assertIsNone(live._youtube_video_id("https://twitch.tv/streamer"))

    def test_hls_segments_parsing(self):
        sample_m3u8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:100
#EXTINF:6.000,
segment100.ts
#EXTINF:5.500,
segment101.ts
#EXTINF:6.000,
segment102.ts
"""
        segs = media.segments(sample_m3u8)
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[100], (6.0, "segment100.ts"))
        self.assertEqual(segs[101], (5.5, "segment101.ts"))
        self.assertEqual(segs[102], (6.0, "segment102.ts"))

    def test_window_playlists(self):
        v_list = {
            10: (6.0, "v10.ts"),
            11: (6.0, "v11.ts"),
            12: (6.0, "v12.ts"),
            13: (6.0, "v13.ts"),
        }
        a_list = {
            9: (6.0, "a9.ts"),
            10: (6.0, "a10.ts"),
            11: (6.0, "a11.ts"),
            12: (6.0, "a12.ts"),
            13: (6.0, "a13.ts"),
        }
        # window of 15 seconds should pick segments 11, 12, 13 (total 18s)
        playlists, covered = media.window_playlists([v_list, a_list], 15.0)
        self.assertEqual(len(playlists), 2)
        self.assertAlmostEqual(covered, 18.0)
        self.assertIn("v11.ts", playlists[0])
        self.assertIn("a11.ts", playlists[1])
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:11", playlists[0])
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:11", playlists[1])

    def test_live_manifest_selection(self):
        info_muxed = {
            "formats": [
                {"protocol": "m3u8", "url": "http://muxed.m3u8", "vcodec": "h264", "acodec": "aac", "height": 720, "tbr": 2500}
            ]
        }
        v_url, a_url = media.live_manifest(info_muxed)
        self.assertEqual(v_url, "http://muxed.m3u8")
        self.assertIsNone(a_url)

        info_separate = {
            "formats": [
                {"protocol": "m3u8", "url": "http://v720.m3u8", "vcodec": "h264", "acodec": "none", "height": 720, "tbr": 2500},
                {"protocol": "m3u8", "url": "http://a128.m3u8", "vcodec": "none", "acodec": "aac", "height": None, "tbr": 128}
            ]
        }
        v_url, a_url = media.live_manifest(info_separate)
        self.assertEqual(v_url, "http://v720.m3u8")
        self.assertEqual(a_url, "http://a128.m3u8")

    def test_session_clip_ids(self):
        job = {
            "id": "test_j1",
            "dir": "/tmp/test_j1",
            "source": {"url": "https://twitch.tv/test"},
            "clips": [{"id": "clip01"}, {"id": "clip02"}],
            "options": {"captions": True, "hinglish": True}
        }
        settings = {"style": "bounce", "layout": "blur", "buffer_min": 20}
        s = live.Session(job, settings)
        self.assertEqual(s.n, 2)

        # Adding a clip should increment n to 3 and propagate hinglish
        added_clip = {"start": 10.0, "end": 20.0, "score": 85, "reason": "Test"}
        # Mock jobs.submit_live and jobs.save
        orig_save = jobs.save
        orig_submit = jobs.submit_live
        jobs.save = lambda j: None
        jobs.submit_live = lambda fn: None
        try:
            s._add_clip(added_clip)
            self.assertEqual(s.n, 3)
            self.assertEqual(len(job["clips"]), 3)
            self.assertEqual(job["clips"][-1]["id"], "clip003")
            self.assertTrue(job["clips"][-1]["hinglish"])
            self.assertTrue(job["clips"][-1]["captions"])
        finally:
            jobs.save = orig_save
            jobs.submit_live = orig_submit

    def test_hourly_limit_zero_keeps_clipping(self):
        """live_per_hour 0 (the default) never skips a check for having clipped a lot this hour; a set limit still does."""
        import time
        from studio import brain, creators
        self.assertEqual(creators.DEFAULTS["live_per_hour"], 0)
        recent = [{"id": f"clip{i:02d}", "score": 90, "made_at": time.time(), "start": i * 300.0} for i in range(4)]
        words = [{"s": 100.0 + i, "w": "bhai"} for i in range(20)]
        asked = []
        orig = brain.judge_transcript
        brain.judge_transcript = lambda *a, **k: (asked.append(1), ([], 0))[1]
        try:
            for limit, should_ask in ((4, False), (0, True)):
                job = {"id": "j", "dir": "/tmp/j", "source": {"url": "https://twitch.tv/test"}, "clips": list(recent),
                       "options": {}, "creator": None, "title": "Stream", "uploader": "Me", "ai": {}}
                s = live.Session(job, {**creators.DEFAULTS, "live_per_hour": limit})
                s.words, s.t_done = words, 120.0
                asked.clear()
                s._ai_check()
                self.assertEqual(bool(asked), should_ask, s.verdicts[-1]["result"])
                if not should_ask:
                    self.assertEqual(s.verdicts[-1]["result"], "Skipped: 4 clips this hour already")
        finally:
            brain.judge_transcript = orig

    def test_post_text_has_alternatives_seo_tags_and_a_paragraph_break(self):
        from studio import brain, posting
        c = brain._tidy({"score": 80, "hook": "move", "title": "IShowSpeed Can't Handle This GTA 6 Chase 😂",
                         "description": "The chase slows down 😂\n\nWatch more IShowSpeed GTA 6 gameplay!",
                         "hashtags": ["IShowSpeed", "GTA6"], "alt_titles": ["One", "Two", "Three", "Four", "Five"],
                         "title_why": "Name, game and feeling.", "seo_tags": ["IShowSpeed GTA 6", "gta 6", "#GTA 6"]}, 1, 20)
        self.assertEqual(c["alt_titles"], ["One", "Two", "Three", "Four"])
        self.assertEqual(c["seo_tags"], ["ishowspeed gta 6", "gta 6"])
        self.assertIn("\n\nWatch more", c["description"])
        text = posting._caption(c, {"post_footer": ""}, shorts=True)
        self.assertTrue(text.endswith("#IShowSpeed #GTA6 #Shorts"), text)
        self.assertNotIn("#Shorts", posting._caption(c, {"post_footer": ""}))

    def test_clips_from_the_first_grab_are_not_cut_to_the_live_recording_length(self):
        """A live job's duration becomes the recording's running length; a clip found 860 s into the 15-minute first
        grab must still be cut against that file, or its end lands before its start (ffmpeg: -t must be non-negative)."""
        job = {"duration": 127.0, "source_duration": 898.0}
        self.assertEqual(jobs._source_length(job, Path("/nonexistent.mp4")), 898.0)
        self.assertEqual(jobs._source_length({"duration": 127.0}, Path("/nonexistent.mp4")), 127.0)  # unreadable: old rule

    def test_a_rate_limited_model_hands_over_to_the_next_at_once(self):
        from studio import ai, brain, config
        calls = []

        def call(model, *a):
            calls.append(model)
            if model == "busy":
                raise RuntimeError("HTTP 429 quota")
            return [], 1, 1
        saved = (ai.call, ai.models, config.ai_ready, brain.time.sleep)
        ai.call, ai.models, config.ai_ready = call, lambda: ["busy", "free"], lambda: True
        brain.time.sleep = lambda s: self.fail("waited instead of switching models")
        try:
            out, model, _ = brain.ask_ai("x")
        finally:
            ai.call, ai.models, config.ai_ready, brain.time.sleep = saved
        self.assertEqual((model, calls), ("free", ["busy", "free"]))

    def test_fallback_chain_skips_a_failing_tool_and_wraps_one_item_in_a_list(self):
        from studio import fallback
        saved = (fallback.TOOLS, fallback.ORDER)
        def broken(text, timeout): raise RuntimeError("not signed in")
        fallback.TOOLS = {"a": (None, broken), "b": (None, lambda text, timeout: '{"title": "ok"}')}
        fallback.ORDER = ["a", "b"]
        try:
            out, name = fallback.ask("x", {"type": "ARRAY", "items": {"type": "OBJECT"}})
        finally:
            fallback.TOOLS, fallback.ORDER = saved
        self.assertEqual((out, name), ([{"title": "ok"}], "b"))

    def test_publish_hinglish(self):
        job = {
            "id": "test_j2",
            "dir": "/tmp/test_j2",
            "source": {"url": "https://twitch.tv/test"},
            "clips": [],
            "options": {"captions": True, "hinglish": True}
        }
        settings = {"style": "bounce", "layout": "blur", "buffer_min": 20}
        s = live.Session(job, settings)
        s.words = [
            {"w": "नमस्ते", "s": 1.0, "e": 1.5},
            {"w": "दोस्त", "s": 1.6, "e": 2.0}
        ]
        s.t_done = 5.0
        orig_save = jobs.save
        jobs.save = lambda j: None
        try:
            s._publish()
            lines = job["live"]["lines"]
            self.assertTrue(len(lines) > 0)
            # The line should be transliterated into Latin characters
            self.assertIn("namaste", lines[0].lower())
        finally:
            jobs.save = orig_save


if __name__ == "__main__":
    unittest.main()
