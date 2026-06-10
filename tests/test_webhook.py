#!/usr/bin/env python3
"""Offline tests for plex_webhook.py (multipart parse + event dispatch)."""
import os
import sys
import types
import unittest
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(
    HERE, "..", "src", "usr", "local", "emhttp", "plugins",
    "cache-mover", "scripts", "plex_webhook.py")

spec = importlib.util.spec_from_file_location("plex_webhook", SCRIPT)
wh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wh)


def multipart(payload_json: str, boundary="abc123"):
    body = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="payload"\r\n\r\n'
        "%s\r\n"
        "--%s\r\n"
        'Content-Disposition: form-data; name="thumb"; filename="t.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
        "\xff\xd8\xff\x00binary\r\n"
        "--%s--\r\n" % (boundary, payload_json, boundary, boundary)
    ).encode("latin-1")
    ct = "multipart/form-data; boundary=%s" % boundary
    return body, ct


class TestParse(unittest.TestCase):
    def test_extracts_payload_field(self):
        body, ct = multipart('{"event":"media.play"}')
        got = wh.parse_multipart_payload(body, ct)
        self.assertEqual(got, '{"event":"media.play"}')

    def test_bad_content_type(self):
        self.assertIsNone(wh.parse_multipart_payload(b"x", "application/json"))


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.calls = []
        # stub the Plex API lookup and the fill subprocess
        wh.resolve_files = lambda rk, args: ["/data/Films/M (2009)/M.mkv"]
        self._orig_popen = wh.subprocess.Popen
        wh.subprocess.Popen = lambda argv: self.calls.append(argv)
        self.args = types.SimpleNamespace(fill="/x/cache_mover_fill")

    def tearDown(self):
        wh.subprocess.Popen = self._orig_popen

    def test_play_triggers_fill(self):
        files = wh.handle_payload(
            '{"event":"media.play","Metadata":{"ratingKey":"123"}}', self.args)
        self.assertEqual(files, ["/data/Films/M (2009)/M.mkv"])
        self.assertEqual(self.calls,
                         [["/x/cache_mover_fill", "/data/Films/M (2009)/M.mkv"]])

    def test_resume_triggers_fill(self):
        files = wh.handle_payload(
            '{"event":"media.resume","Metadata":{"ratingKey":"9"}}', self.args)
        self.assertEqual(len(files), 1)
        self.assertEqual(len(self.calls), 1)

    def test_pause_ignored(self):
        files = wh.handle_payload(
            '{"event":"media.pause","Metadata":{"ratingKey":"9"}}', self.args)
        self.assertEqual(files, [])
        self.assertEqual(self.calls, [])

    def test_no_rating_key(self):
        files = wh.handle_payload('{"event":"media.play"}', self.args)
        self.assertEqual(files, [])
        self.assertEqual(self.calls, [])

    def test_garbage_payload(self):
        self.assertEqual(wh.handle_payload("not json", self.args), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
