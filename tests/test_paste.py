"""Pasting into the group name field reads the clipboard bounded."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launcher_core  # noqa: E402


class PasteTests(unittest.TestCase):
    def run_with(self, replies):
        calls = []

        def fake(command, max_bytes=0, timeout=0, binary=False):
            calls.append((command, max_bytes, timeout))
            return replies.get(command[-1])
        with mock.patch.object(launcher_core, "read_bounded_output", fake), \
             mock.patch.object(launcher_core.shutil, "which", return_value="/usr/bin/wl-paste"):
            return launcher_core.clipboard_text(40), calls

    def test_text_is_cleaned_and_capped(self):
        text, calls = self.run_with({"--list-types": "image/png\ntext/plain\n",
                                     "text/plain": "Work\r\n\x1b[31mstuff‮ " + "x" * 100})
        self.assertNotIn("\x1b", text)
        self.assertLessEqual(len(text), 40)
        self.assertTrue(text.startswith("Work [31mstuff"))
        self.assertTrue(all(c[1] <= launcher_core.MAX_PASTE_TEXT_BYTES or c[0][-1] == "--list-types" for c in calls))
        self.assertTrue(all(c[2] <= 2.0 for c in calls))

    def test_empty_clipboard_and_oversized_offer(self):
        self.assertEqual(self.run_with({"--list-types": None})[0], "")       # wl-paste fails when empty
        self.assertEqual(self.run_with({"--list-types": "image/png\n"})[0], "")
        self.assertIsNone(self.run_with({"--list-types": "text/plain\n", "text/plain": None})[0])


class PasteLimitTests(unittest.TestCase):
    """Each field pastes up to its own limit: the screenshot note 1000 characters."""
    def setUp(self):
        import ast
        from types import SimpleNamespace
        source = ROOT / "launcher_popup.py"
        tree = ast.parse(source.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Launcher")
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("paste_limit", "on_field_paste")]
        self.calls = []
        thread = lambda target, daemon=True: SimpleNamespace(start=target)
        namespace = {"groups": SimpleNamespace(MAX_NAME=40), "MAX_PASTE_TEXT_BYTES": 4096,
                     "threading": SimpleNamespace(Thread=thread), "GLib": SimpleNamespace(idle_add=mock.Mock()),
                     "clipboard_text": lambda chars, max_bytes: self.calls.append((chars, max_bytes)) or "x"}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), "exec"), namespace)
        self.app = namespace["Launcher"]()
        self.app.insert_pasted = mock.Mock()

    def entry(self, limit, delegate=False):
        field = mock.Mock(spec=["get_max_length"])
        field.get_max_length.return_value = limit
        if not delegate:
            return field
        search = mock.Mock(spec=["get_delegate"])
        search.get_delegate.return_value = field
        return search

    def test_note_pastes_up_to_1000_characters(self):
        self.app.on_field_paste(mock.Mock(), self.entry(1000))
        self.assertEqual(self.calls, [(1000, 4096)])

    def test_other_fields_keep_their_limits(self):
        self.app.on_field_paste(mock.Mock(), self.entry(0))                 # a field without its own limit
        self.app.on_field_paste(mock.Mock(), self.entry(80, delegate=True))  # icon search
        self.app.on_field_paste(mock.Mock(), self.entry(0, delegate=True))   # filter field
        self.assertEqual(self.calls, [(40, 4096), (80, 4096), (40, 4096)])
        # (The group name field's own limit is groups.MAX_NAME, the filter field's 200.)


if __name__ == "__main__":
    unittest.main()
