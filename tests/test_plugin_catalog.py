"""The Information page's plugin list from the marketplace catalog (plugin_catalog)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import plugin_catalog as pc

CATALOG = {"plugins": [
    {"id": "ubruckhaus.wlan-monitor", "name": "WLAN Monitor", "repo": "https://github.com/uBruckhaus/omarchy-wlan",
     "description": "Who is on your Wi-Fi.", "version": "0.7.1", "verificationStatus": "verified"},
    {"id": "ubruckhaus.new-thing", "name": "New Thing", "repo": "https://github.com/uBruckhaus/new-thing",
     "description": "Does a new thing for you. And more besides.", "verificationStatus": "unverified"},
    {"id": "someone.other", "name": "Other", "repo": "https://github.com/someone/other", "description": "x"},
    {"id": "../bad", "name": "Bad", "repo": "https://github.com/uBruckhaus/bad"},
]}


def answer(status, body=b"", etag='"abc"'):
    return f"HTTP/2 {status}\r\netag: {etag}\r\ncontent-type: text/plain\r\n\r\n".encode() + body


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "plugin-catalog.json"

    def test_only_the_authors_plugins(self):
        found = pc.parse(CATALOG)
        self.assertEqual([p["id"] for p in found], ["ubruckhaus.wlan-monitor", "ubruckhaus.new-thing"])
        self.assertTrue(found[0]["verified"])

    def test_read_then_not_changed_then_offline(self):
        asked = []

        def fetch(etag):
            asked.append(etag)
            return answer(200, json.dumps(CATALOG).encode())
        self.assertEqual(len(pc.refresh(fetch, self.cache, now=1000)), 2)
        self.assertEqual(asked, [""])
        self.assertEqual(len(pc.refresh(fetch, self.cache, now=1500)), 2)       # within the hour: no request
        self.assertEqual(len(asked), 1)

        def not_changed(etag):
            asked.append(etag)
            return answer(304)
        self.assertEqual(len(pc.refresh(not_changed, self.cache, now=9000)), 2)
        self.assertEqual(asked[-1], '"abc"')

        def offline(etag):
            raise OSError("no network")
        self.assertEqual(len(pc.refresh(offline, self.cache, now=99000)), 2)    # the cached list

    def test_error_answer_keeps_the_cached_list(self):
        pc.refresh(lambda etag: answer(200, json.dumps(CATALOG).encode()), self.cache, now=1000)
        self.assertEqual(len(pc.refresh(lambda etag: answer(503, b"busy"), self.cache, now=9000)), 2)

    def test_cards_known_first_new_after(self):
        cards = pc.cards(pc.parse(CATALOG))
        self.assertEqual(cards[0][0], "ubruckhaus.wlan-monitor")
        self.assertEqual(cards[0][3], "Who is on your Wi-Fi, with a network map")    # the built-in one-liner
        self.assertEqual(cards[1], ("ubruckhaus.new-thing", "New Thing", pc.NEW_GLYPH, "Does a new thing for you."))
        self.assertEqual(len(pc.cards([])), len(pc.more_plugins.PLUGINS))          # nothing read yet

    def test_redirect_headers(self):
        raw = b"HTTP/1.1 301 Moved\r\nlocation: x\r\n\r\n" + answer(200, b"{}")
        self.assertEqual(pc.split_response(raw), (200, {"etag": '"abc"', "content-type": "text/plain"}, b"{}"))

    def test_odd_etag_is_not_sent(self):
        self.assertNotIn("--header", pc.curl_command('"x"\r\nEvil: 1'))
        self.assertIn('If-None-Match: "abc"', pc.curl_command('"abc"'))

    def test_edited_cache_entries_are_dropped(self):
        self.cache.write_text(json.dumps({"checked": 1, "plugins": [{"id": "../x", "name": "X"}, "junk",
                                                                   {"id": "ubruckhaus.ok", "name": "Ok"}]}))
        self.assertEqual([p["id"] for p in pc.load(self.cache)["plugins"]], ["ubruckhaus.ok"])

    def test_fifo_symlink_and_oversized_cache_are_not_read(self):
        os.mkfifo(self.cache)
        self.assertEqual(pc.load(self.cache), {})                       # returns at once, no writer needed
        self.cache.unlink()
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(json.dumps({"plugins": [{"id": "ubruckhaus.x", "name": "X"}]}))
        self.cache.symlink_to(target)
        self.assertEqual(pc.load(self.cache), {})
        self.cache.unlink()
        self.cache.write_text(json.dumps({"plugins": [], "pad": "x" * (pc.MAX_CACHE_BYTES + 1)}))
        self.assertEqual(pc.load(self.cache), {})

    def test_bad_times_count_as_stale(self):
        self.cache.write_text(json.dumps({"plugins": [], "checked": "bad", "tried": [1]}))
        asked = []
        pc.refresh(lambda etag: asked.append(etag) or answer(200, json.dumps(CATALOG).encode()), self.cache, now=5000)
        self.assertEqual(len(asked), 1)

    def test_failures_are_tried_at_most_once_an_hour(self):
        asked = []

        def offline(etag):
            asked.append(etag)
            raise OSError("no network")
        self.assertEqual(pc.refresh(offline, self.cache, now=1000), [])
        self.assertEqual(pc.refresh(offline, self.cache, now=1500), [])
        self.assertEqual(len(asked), 1)
        pc.refresh(lambda etag: answer(503, b"busy"), self.cache, now=1000 + pc.MAX_AGE)
        self.assertEqual(pc.load(self.cache)["tried"], 1000 + pc.MAX_AGE)
        found = pc.refresh(lambda etag: answer(200, json.dumps(CATALOG).encode()), self.cache, now=1000 + 2 * pc.MAX_AGE)
        self.assertEqual(len(found), 2)

    def test_cached_entries_get_the_network_limits(self):
        self.cache.write_text(json.dumps({"checked": 1, "plugins": [
            {"id": "ubruckhaus.long", "name": "N" * 500, "description": "d" * 5000, "verified": "yes"}]}))
        entry = pc.load(self.cache)["plugins"][0]
        self.assertEqual((len(entry["name"]), len(entry["description"]), entry["verified"]), (60, 600, False))

    def test_huge_numbers_and_deep_nesting_are_an_invalid_cache(self):
        self.cache.write_text('{"plugins": [], "checked": 1' + "0" * 400 + "}")
        self.assertEqual(pc.load(self.cache)["checked"], 0.0)
        self.cache.write_text('{"plugins": ' + "[" * 100000 + "]" * 100000 + "}")
        self.assertEqual(pc.load(self.cache), {})

    def test_hourly_limit_holds_without_a_writable_cache(self):
        asked = []

        def offline(etag):
            asked.append(etag)
            raise OSError("no network")
        blocked = Path(self.tmp.name) / "file-not-dir" / "plugin-catalog.json"
        blocked.parent.write_text("")                        # the cache folder cannot be made
        pc.refresh(offline, blocked, now=1000)
        pc.refresh(offline, blocked, now=1001)
        self.assertEqual(len(asked), 1)

    def test_lone_surrogates_become_plain_text(self):
        self.cache.write_text('{"checked": 1, "plugins": [{"id": "ubruckhaus.x", "name": "A\\ud800", '
                              '"description": "\\udfff d"}]}')
        entry = pc.load(self.cache)["plugins"][0]
        self.assertEqual((entry["name"], entry["description"]), ("A?", "? d"))
        entry["name"].encode("utf-8")                         # what GTK does with it
        catalog = {"plugins": [{"id": "ubruckhaus.y", "name": "\ud800", "repo": "https://github.com/uBruckhaus/y"}]}
        self.assertEqual(pc.parse(catalog)[0]["name"], "?")

    def test_a_malformed_catalog_keeps_the_list_read_before(self):
        pc.refresh(lambda etag: answer(200, json.dumps(CATALOG).encode()), self.cache, now=1000)
        for body in (b"{}", b"[]", b'{"plugins": "invalid"}'):
            now = 1000 + pc.MAX_AGE * (2 + len(body))
            self.assertEqual(len(pc.refresh(lambda etag: answer(200, body), self.cache, now=now)), 2)
            self.assertEqual(len(pc.load(self.cache)["plugins"]), 2)
        # A catalog that really lists none of the author's plugins is believed.
        self.assertEqual(pc.refresh(lambda etag: answer(200, b'{"plugins": []}'), self.cache, now=10 ** 6), [])


if __name__ == "__main__":
    unittest.main()
