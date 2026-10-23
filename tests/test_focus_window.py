"""Switch to window: Hyprland's Lua dispatcher (every Omarchy 4.x), the classic one as a fallback."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import desktop_omarchy


class FocusWindowTests(unittest.TestCase):
    def focus(self, client, codes):
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=codes[len(calls) - 1], stdout="")
        thread = lambda target, daemon=True: SimpleNamespace(start=target)
        with patch.object(desktop_omarchy.subprocess, "run", side_effect=run), \
                patch("threading.Thread", thread):
            desktop_omarchy.focus_window(client)
        return calls

    def test_lua_dispatch(self):
        calls = self.focus({"address": "0x5a1"}, [0])
        self.assertEqual(calls, [["hyprctl", "dispatch", 'hl.dsp.focus({window = "address:0x5a1"})']])

    def test_classic_dispatch_when_lua_is_refused(self):
        calls = self.focus({"address": "0x5a1"}, [7, 0])
        self.assertEqual(calls[1], ["hyprctl", "dispatch", "focuswindow", "address:0x5a1"])

    def test_odd_address_is_not_sent(self):
        self.assertEqual(self.focus({"address": '0x1"}) hl.exec_cmd("x'}, []), [])
        self.assertEqual(self.focus({}, []), [])


if __name__ == "__main__":
    unittest.main()
