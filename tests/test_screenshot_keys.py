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
        names = {"on_key", "settings_open", "shot_dialog_open", "screenshot_key_on", "finish_screenshot_note",
                 "note_field_lacks_focus", "on_shortcut_key", "show_screenshot_note"}
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.settings = {}
        self.screenshots = SimpleNamespace(key_enabled=lambda s: s.get("screenshot_key") is not False,
                                           save_note=Mock(), MAX_NOTE=1000)
        self.glib = SimpleNamespace(idle_add=Mock())
        gdk = Keys(KEY_Escape=27, KEY_BackSpace=8, KEY_Return=13, KEY_KP_Enter=65421,
                              KEY_ISO_Enter=65076, KEY_F12=65481, KEY_a=97,
                              ModifierType=SimpleNamespace(CONTROL_MASK=CONTROL, SHIFT_MASK=SHIFT,
                                                           ALT_MASK=ALT, SUPER_MASK=SUPER),
                   keyval_to_unicode=lambda key: key if 32 <= key < 127 else 0)
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
        app.window = Mock()
        app.window.get_focus.return_value = None
        self.app = app

    def open_dialog(self, path=Path("/tmp/shot.png"), text="", focus_in_field=True):
        self.app.shot_dialog = Mock()
        self.app.shot_dialog.get_visible.return_value = True
        self.app.shot_path = path
        self.app.shot_note_entry = Mock()
        self.app.shot_note_entry.get_text.return_value = text
        if focus_in_field:
            self.app.window.get_focus.return_value = self.app.shot_note_entry

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

    def test_keys_on_a_dialog_button_go_to_the_note(self):
        self.open_dialog(text="wron", focus_in_field=False)
        button = Mock()
        button.is_ancestor.return_value = False
        self.app.window.get_focus.return_value = button
        self.assertTrue(self.app.on_key(None, ord("g"), 0, 0))
        self.app.shot_note_entry.grab_focus.assert_called()
        self.app.shot_note_entry.set_text.assert_called_with("wrong")
        self.assertTrue(self.app.on_key(None, 8, 0, 0))
        self.app.shot_note_entry.set_text.assert_called_with("wro")
        self.assertFalse(self.app.on_key(None, ord("g"), 0, CONTROL))   # Ctrl+G is no text

    def test_shortcut_recorder_leaves_the_note_dialog_its_keys(self):
        self.app.shortcut_app = {"desktop_id": "x.desktop"}
        self.open_dialog()
        for key in (27, 13, 8, ord("a")):
            self.assertFalse(self.app.on_shortcut_key(None, key, 0, 0))

    def test_focus_goes_back_where_it_was(self):
        field = Mock()
        field.get_mapped.return_value = True
        field.is_sensitive.return_value = True
        order = []
        self.app.window.get_focus.return_value = field
        self.app.actions_menu_open = Mock(return_value=False)
        self.app.shot_dialog = Mock()
        self.app.shot_dialog.get_visible.return_value = False
        self.app.shot_path_label = Mock()
        self.app.shot_note_entry = Mock()
        self.app.show_screenshot_note(Path("/tmp/shot.png"))
        self.app.shot_dialog.get_visible.return_value = True
        self.app.shot_note_entry.get_text.return_value = ""
        self.app.stack.set_sensitive.side_effect = lambda on: order.append(("stack", on))
        self.app.shot_dialog.set_visible.side_effect = lambda on: order.append(("dialog", on))
        field.grab_focus.side_effect = lambda: order.append(("focus", True))
        self.app.finish_screenshot_note(False)
        self.assertEqual(order, [("stack", True), ("dialog", False), ("focus", True)])

    def test_a_failed_note_is_reported_and_the_dialog_closes(self):
        self.open_dialog(text="x")
        self.screenshots.save_note.side_effect = OSError("read-only")
        self.app.finish_screenshot_note(True)
        self.app.notify.assert_called_once()
        self.app.shot_dialog.set_visible.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
