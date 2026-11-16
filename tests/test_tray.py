"""Tray icons: menu parsing, action ids and their shortcuts."""
import unittest
from unittest import mock

import shortcuts
import tray

LAYOUT = [
    (21, {"label": "Not secured", "enabled": False}, []),
    (22, {"label": "_Secure my connection"}, []),
    (23, {"label": "All connections", "children-display": "submenu"}, [
        (24, {"label": "Countries:", "enabled": False}, []),
        (25, {"label": "Germany"}, []),
    ]),
    (26, {"type": "separator"}, []),
    (27, {"label": "Tray icon", "toggle-type": "checkmark", "toggle-state": 1}, []),
    (28, {"label": "Hidden", "visible": False}, []),
]
STEAM = {"name": "Steam", "tray_id": "steam", "desktop_id": "tray:steam", "actions": [], "exec": ""}


class TrayMenuTests(unittest.TestCase):
    def test_menu_nodes_keep_structure_and_drop_mnemonics(self):
        nodes = tray._menu_nodes(LAYOUT)
        self.assertEqual([n.get("label") for n in nodes],
                         ["Not secured", "Secure my connection", "All connections", None, "Tray icon"])
        self.assertEqual(nodes[2]["children"][1]["action"], "all-connections.germany")
        self.assertTrue(nodes[4]["checked"])
        self.assertEqual(nodes[4]["toggle"], "checkmark")

    def test_flat_actions_are_clickable_leaves(self):
        actions = tray.flat_actions(tray._menu_nodes(LAYOUT))
        self.assertEqual([(a["id"], a["name"]) for a in actions], [
            ("secure-my-connection", "Secure my connection"),
            ("all-connections.germany", "All connections › Germany"),
            ("tray-icon", "Tray icon")])
        for action in actions:
            self.assertRegex(action["id"], shortcuts._ACTION_RE)

    def test_run_clicks_the_entry_named_by_the_shortcut(self):
        item = {"tray_id": "NordVPN", "tray_menu": tray._menu_nodes(LAYOUT)}
        with mock.patch.object(tray, "list_items", return_value=[item]), \
                mock.patch.object(tray, "click", return_value=True) as click, \
                mock.patch.object(tray, "activate", return_value=True) as activate:
            self.assertTrue(tray.run("NordVPN", "all-connections.germany"))
            click.assert_called_once_with(item, 25)
            self.assertFalse(tray.run("NordVPN", "not-secured"))  # Disabled entries stay disabled.
            self.assertFalse(tray.run("Steam", "library"))
            self.assertTrue(tray.run("NordVPN"))
            activate.assert_called_once_with(item)

    def test_cli_rejects_bad_ids(self):
        self.assertEqual(tray.main(["tray.py", "a b"]), 2)
        self.assertEqual(tray.main(["tray.py"]), 2)


class TrayBudgetTests(unittest.TestCase):
    """Review #14102: many slow tray providers must not hold the reader."""

    def slow_calls(self, delay):
        import time

        def call(bus, name, path, interface, method, args=None, reply=None):
            timeout = tray._timeout_ms() / 1000         # what the real call would be given
            time.sleep(min(delay, timeout))
            if delay > timeout:
                raise tray.GLib.Error("timed out")
            if method == "GetAll":
                return tray.GLib.Variant("(a{sv})", ({"Id": tray.GLib.Variant("s", name.strip(":").replace(".", "-")),
                                                       "Title": tray.GLib.Variant("s", name)},))
            raise tray.GLib.Error("no menu")
        return call

    def test_many_slow_items_end_within_the_budget(self):
        import time
        entries = [f":1.{n}/StatusNotifierItem" for n in range(500)]
        with mock.patch.object(tray, "_bus"), mock.patch.object(tray, "_registered", return_value=entries), \
                mock.patch.object(tray, "_owner_programs", return_value=set()), \
                mock.patch.object(tray, "_call", side_effect=self.slow_calls(1.4)) as call:
            start = time.monotonic()
            items = tray.list_items(budget=0.5)
            self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(items, [])
        self.assertLessEqual(call.call_count, 2)
        self.assertIsNone(getattr(tray._local, "deadline", None))     # budget cleared afterwards
        self.assertEqual(tray._timeout_ms(), tray.TIMEOUT_MS)

    def test_at_most_max_items_are_asked(self):
        entries = [f":1.{n}/StatusNotifierItem" for n in range(500)]
        with mock.patch.object(tray, "_bus"), mock.patch.object(tray, "_registered", return_value=entries), \
                mock.patch.object(tray, "_owner_programs", return_value=set()), \
                mock.patch.object(tray, "_call", side_effect=self.slow_calls(0)):
            items = tray.list_items()
        self.assertEqual(len(items), tray.MAX_ITEMS)

    def test_fast_items_read_before_the_budget_ends_are_kept(self):
        import time
        entries = [f":1.{n}/StatusNotifierItem" for n in range(10)]
        delays = iter([0, 0, 0, 0] + [5] * 100)   # GetAll + menu calls of the first items are quick

        def call(bus, name, path, interface, method, args=None, reply=None):
            delay = next(delays)
            timeout = tray._timeout_ms() / 1000
            time.sleep(min(delay, timeout))
            if delay > timeout:
                raise tray.GLib.Error("timed out")
            if method == "GetAll":
                return tray.GLib.Variant("(a{sv})", ({"Id": tray.GLib.Variant("s", "app" + name[3:]),
                                                       "Title": tray.GLib.Variant("s", name)},))
            raise tray.GLib.Error("no menu")
        with mock.patch.object(tray, "_bus"), mock.patch.object(tray, "_registered", return_value=entries), \
                mock.patch.object(tray, "_owner_programs", return_value=set()), \
                mock.patch.object(tray, "_call", side_effect=call):
            start = time.monotonic()
            items = tray.list_items(budget=0.4)
            self.assertLess(time.monotonic() - start, 1.0)
        self.assertGreaterEqual(len(items), 1)


class TrayBoundsTests(unittest.TestCase):
    """Marketplace review #14102 round 2: replies are walked with limits, never unpacked whole."""

    @staticmethod
    def node(i, label, children=()):
        from gi.repository import GLib
        return GLib.Variant("(ia{sv}av)", (i, {"label": GLib.Variant("s", label)}, list(children)))

    def test_menu_is_cut_by_count_and_depth(self):
        from gi.repository import GLib
        wide = GLib.Variant("av", [self.node(i, f"Entry {i}") for i in range(5000)])
        flat = tray._layout_children(wide, [tray.MAX_MENU_NODES], 0)
        self.assertEqual(len(flat), tray.MAX_MENU_NODES)
        deep = self.node(0, "leaf")
        for i in range(1, 50):
            deep = self.node(i, f"level {i}", [deep])
        tree = tray._layout_children(GLib.Variant("av", [deep]), [tray.MAX_MENU_NODES], 0)
        depth = 0
        while tree:
            depth += 1
            tree = tree[0][2]
        self.assertEqual(depth, tray.MAX_MENU_DEPTH)
        odd = GLib.Variant("av", [GLib.Variant("s", "junk")])
        self.assertEqual(tray._layout_children(odd, [10], 0), [])

    def test_menu_walk_stops_at_the_list_deadline(self):
        from gi.repository import GLib
        import time
        wide = GLib.Variant("av", [self.node(i, "x") for i in range(50)])
        tray._local.deadline = time.monotonic() - 1
        try:
            with self.assertRaises(tray.BudgetUsedUp):
                tray._layout_children(wide, [100], 0)
        finally:
            tray._local.deadline = None

    def test_registration_is_capped(self):
        from gi.repository import GLib
        reply = GLib.Variant("(v)", (GLib.Variant("as", [f":1.{i}/item" for i in range(10000)]),))
        with mock.patch.object(tray, "_call", return_value=reply):
            entries = tray._registered(None)
        self.assertEqual(len(entries), tray.MAX_REGISTERED)
        bad = GLib.Variant("(v)", (GLib.Variant("s", "x"),))
        with mock.patch.object(tray, "_call", return_value=bad):
            self.assertEqual(tray._registered(None), [])


class TrayKeyTests(unittest.TestCase):
    def test_electron_icons_go_by_title(self):
        taken = set()
        first = tray.item_key("chrome_status_icon_1", "LM Studio", taken)
        taken.add(first)
        second = tray.item_key("chrome_status_icon_1", "Antigravity", taken)
        self.assertEqual((first, second), ("lm-studio", "antigravity"))
        self.assertEqual(tray.item_key("steam", "Steam", set()), "steam")
        # The same Id twice: the second one is told apart by its title.
        self.assertEqual(tray.item_key("app", "Other App", {"app"}), "other-app")
        self.assertIsNone(tray.item_key("", "", set()))

    def test_title_falls_back_to_tooltip(self):
        self.assertEqual(tray._title({"Title": "Steam"}), "Steam")
        self.assertEqual(tray._title({"Title": "", "ToolTip": ("", [], "LM Studio", "")}), "LM Studio")
        self.assertEqual(tray._title({"Title": ""}), "")


class TrayShortcutTests(unittest.TestCase):
    def test_tray_shortcut_runs_tray_script_directly(self):
        launch = shortcuts.app_launch_argv(STEAM)
        self.assertEqual(launch, [shortcuts.TRAY_SCRIPT, "steam"])
        self.assertEqual(shortcuts.tray_launch_id(launch), "steam")
        state = {"apps": {"tray:steam": {"name": "Steam", "launch": launch, "disabled": [], "shortcuts": [
            {"mods": ["SUPER", "ALT"], "key": "L", "replaces": "", "action": "library"},
            {"mods": ["SUPER", "ALT"], "key": "S", "replaces": "", "action": ""}]}}}
        lua = shortcuts.render_lua(state)
        self.assertIn(f'"{shortcuts.TRAY_SCRIPT} steam library"', lua)
        self.assertIn(f'"{shortcuts.TRAY_SCRIPT} steam"', lua)
        self.assertNotIn("uwsm-app", lua)
        self.assertNotIn("launch =", lua)

    def test_tray_icons_have_no_external_shortcuts(self):
        bindings = shortcuts.Bindings({"apps": {}}, live=[], user=([], set()), defaults=[])
        self.assertEqual(bindings.external_shortcuts(STEAM), [])


if __name__ == "__main__":
    unittest.main()
