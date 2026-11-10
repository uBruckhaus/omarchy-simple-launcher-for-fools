"""F12 and the screenshot note in the popup's key handling, without GTK."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

CONTROL, SHIFT, ALT, SUPER = 4, 1, 8, 64


class Keys(SimpleNamespace):
    """Gdk: the keys named here, any other key a value no test presses."""
    def __getattr__(self, name):
        if name.startswith("KEY_"):
            return 10_000_000 + sum(map(ord, name))
        raise AttributeError(name)


class ScreenshotKeyTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "launcher_popup.py"
        tree = ast.parse(source.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Launcher")
        names = {"on_key", "settings_open", "shot_dialog_open", "screenshot_key_on", "finish_screenshot_note"}
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.settings = {}
        self.screenshots = SimpleNamespace(key_enabled=lambda s: s.get("screenshot_key") is not False,
                                           save_note=Mock())
        self.glib = SimpleNamespace(idle_add=Mock())
        gdk = Keys(KEY_Escape=27, KEY_BackSpace=8, KEY_Return=13, KEY_KP_Enter=65421,
                              KEY_ISO_Enter=65076, KEY_F12=65481, KEY_a=97,
                              ModifierType=SimpleNamespace(CONTROL_MASK=CONTROL, SHIFT_MASK=SHIFT,
                                                           ALT_MASK=ALT, SUPER_MASK=SUPER),
                   keyval_to_unicode=lambda _key: 0)
        namespace = {"Gdk": gdk, "GLib": self.glib, "POPUP": True, "screenshots": self.screenshots,
                     "screenshot_settings": lambda: self.settings, "_": lambda text: text}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), "exec"), namespace)
        self.namespace = namespace
        app = namespace["Launcher"]()
        app.shortcut_app = None
        app.stack = Mock()
        app.stack.get_visible_child_name.return_value = "list"
        app.header_bar = Mock()
        app.take_screenshot = Mock()
        app.close_settings_page = Mock()
        app.notify = Mock()
        app.overview, app.search_query, app.groups = False, "", {"collapsed": set()}
        app.rows = Mock()
        app.rows.get_selected_row.return_value = None
        app.actions_menu_open = Mock(return_value=False)
        app.open_folder = Mock(return_value=None)
        self.app = app

    def open_dialog(self, path=Path("/tmp/shot.png"), text=""):
        self.app.shot_dialog = Mock()
        self.app.shot_dialog.get_visible.return_value = True
        self.app.shot_path = path
        self.app.shot_note_entry = Mock()
        self.app.shot_note_entry.get_text.return_value = text

    def test_f12_alone_takes_a_screenshot(self):
        self.assertTrue(self.app.on_key(None, 65481, 0, 0))
        self.glib.idle_add.assert_called_once_with(self.app.take_screenshot)

    def test_f12_with_a_modifier_or_switched_off_does_nothing(self):
        for state in (CONTROL, ALT, SUPER, SHIFT):
            self.app.on_key(None, 65481, 0, state)
        self.settings["screenshot_key"] = False
        self.app.on_key(None, 65481, 0, 0)
        self.glib.idle_add.assert_not_called()

    def test_f12_not_outside_the_bar_popup(self):
        self.namespace["POPUP"] = False
        self.app.on_key(None, 65481, 0, 0)
        self.glib.idle_add.assert_not_called()

    def test_f12_never_fires_while_a_shortcut_is_recorded(self):
        # The capture-phase handler takes the key first; even if it reached
        # on_key, F12 stays the key being recorded.
        self.app.shortcut_app = {"desktop_id": "x.desktop"}
        self.app.on_key(None, 65481, 0, 0)
        self.glib.idle_add.assert_not_called()

    def test_escape_skips_the_note_enter_keeps_it(self):
        self.open_dialog(text="wrong colour")
        self.assertTrue(self.app.on_key(None, 27, 0, 0))
        self.screenshots.save_note.assert_not_called()
        self.app.close_settings_page.assert_not_called()
        self.open_dialog(text="wrong colour")
        self.assertTrue(self.app.on_key(None, 13, 0, 0))
        self.screenshots.save_note.assert_called_once_with(Path("/tmp/shot.png"), "wrong colour")
        self.app.shot_dialog.set_visible.assert_called_with(False)
        self.app.stack.set_sensitive.assert_called_with(True)

    def test_typing_and_backspace_belong_to_the_note_field(self):
        self.open_dialog()
        self.app.stack.get_visible_child_name.return_value = "settings"
        self.assertFalse(self.app.on_key(None, 97, 0, 0))
        self.assertFalse(self.app.on_key(None, 8, 0, 0))
        self.app.close_settings_page.assert_not_called()

    def test_esc_and_backspace_still_leave_settings_without_the_dialog(self):
        self.app.stack.get_visible_child_name.return_value = "settings"
        self.assertTrue(self.app.on_key(None, 27, 0, 0))
        self.assertTrue(self.app.on_key(None, 8, 0, 0))
        self.assertEqual(self.app.close_settings_page.call_count, 2)

    def test_a_failed_note_is_reported_and_the_dialog_closes(self):
        self.open_dialog(text="x")
        self.screenshots.save_note.side_effect = OSError("read-only")
        self.app.finish_screenshot_note(True)
        self.app.notify.assert_called_once()
        self.app.shot_dialog.set_visible.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
