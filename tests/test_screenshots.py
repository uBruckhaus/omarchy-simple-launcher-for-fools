"""F12 screenshot paths and capture failures, without a compositor."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import screenshots  # noqa: E402


class ScreenshotTests(TestCase):
    def test_f12_is_on_until_turned_off(self):
        self.assertTrue(screenshots.key_enabled({}))
        self.assertTrue(screenshots.key_enabled({"screenshot_key": True}))
        self.assertFalse(screenshots.key_enabled({"screenshot_key": False}))

    def test_folder_setting_accepts_only_absolute_paths(self):
        with patch.object(screenshots, "default_folder", return_value=Path("/tmp/default")):
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "/tmp/shots"}),
                             Path("/tmp/shots"))
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "../shots"}),
                             Path("/tmp/default"))
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "/tmp/../etc"}),
                             Path("/tmp/default"))
            self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": "/tmp/bad\nname"}),
                             Path("/tmp/default"))

    def test_folder_setting_rejects_symlink_component(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real")
            with patch.object(screenshots, "default_folder", return_value=root / "fallback"):
                self.assertEqual(screenshots.folder_from_settings({"screenshot_folder": str(root / "link" / "shots")}),
                                 root / "fallback")

    def test_capture_uses_popup_geometry_and_unique_name(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = datetime(2026, 11, 6, 9, 30, 0)
            (folder / "app-manager-2026-11-06-09-30-00.png").write_bytes(b"old")
            commands = []

            def fake_grim(argv, **_kwargs):
                commands.append(argv)
                Path(argv[-1]).write_bytes(b"PNG")
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim):
                path = screenshots.capture((100, 200, 700, 500), folder, now)
            self.assertEqual(path.name, "app-manager-2026-11-06-09-30-00-2.png")
            self.assertEqual(path.read_bytes(), b"PNG")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(commands[0][0:3], ["grim", "-g", "100,200 700x500"])

    def test_grim_never_writes_into_the_chosen_folder_and_links_are_not_followed(self):
        """As in Theme Manager (its marketplace review 2.0.0): grim writes into a private folder; the copy into the chosen
        folder is created exclusively there and never follows a planted link."""
        with TemporaryDirectory() as tmp:
            folder, victim = Path(tmp) / "shots", Path(tmp) / "victim.txt"
            folder.mkdir(mode=0o755)
            victim.write_bytes(b"keep")
            now = datetime(2026, 11, 7, 22, 0, 0)
            (folder / "app-manager-2026-11-07-22-00-00.png").symlink_to(victim)
            written = []

            def fake_grim(argv, **_kwargs):
                written.append(Path(argv[-1]))
                Path(argv[-1]).write_bytes(b"PNG")
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim):
                path = screenshots.capture((0, 0, 10, 10), folder, now)
            self.assertNotEqual(written[0].parent, folder)                # grim wrote elsewhere
            self.assertFalse(written[0].parent.exists())                  # and that folder is gone
            self.assertEqual(path.name, "app-manager-2026-11-07-22-00-00-2.png")
            self.assertEqual(victim.read_bytes(), b"keep")
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             ["app-manager-2026-11-07-22-00-00-2.png", "app-manager-2026-11-07-22-00-00.png"])

    def test_a_folder_others_can_write_is_refused(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "shared"
            folder.mkdir()
            folder.chmod(0o777)
            with patch.object(screenshots.subprocess, "run") as grim:
                with self.assertRaises(ValueError):
                    screenshots.capture((0, 0, 10, 10), folder)
            grim.assert_not_called()

    def test_failed_capture_removes_temporary_file(self):
        with TemporaryDirectory() as tmp:
            with patch.object(screenshots.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"failed")):
                with self.assertRaises(OSError):
                    screenshots.capture((0, 0, 400, 300), Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_capture_skips_broken_symlink_and_existing_note(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = datetime(2026, 11, 6, 9, 30, 0)
            (folder / "app-manager-2026-11-06-09-30-00.png").symlink_to(folder / "missing")
            (folder / "app-manager-2026-11-06-09-30-00-2.txt").write_text("old")
            def fake_grim(argv, **_kwargs):
                Path(argv[-1]).write_bytes(b"PNG")
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim):
                path = screenshots.capture((0, 0, 400, 300), folder, now)
            self.assertEqual(path.name, "app-manager-2026-11-06-09-30-00-3.png")
            self.assertTrue((folder / "app-manager-2026-11-06-09-30-00.png").is_symlink())

    def test_custom_folder_failure_uses_default(self):
        geometry = (0, 0, 400, 300)
        with patch.object(screenshots, "default_folder", return_value=Path("/tmp/default")), \
             patch.object(screenshots, "capture", side_effect=[OSError("unwritable"), Path("/tmp/default/a.png")]) as capture:
            self.assertEqual(screenshots.capture_to_settings(geometry, {"screenshot_folder": "/tmp/custom"}),
                             Path("/tmp/default/a.png"))
        self.assertEqual([call.args[1] for call in capture.call_args_list],
                         [Path("/tmp/custom"), Path("/tmp/default")])

    def test_optional_note_is_private_and_never_overwritten(self):
        with TemporaryDirectory() as tmp:
            picture = Path(tmp) / "shot.png"
            self.assertIsNone(screenshots.save_note(picture, "  "))
            note = screenshots.save_note(picture, "  wrong color  ")
            self.assertEqual(note.read_text(), "wrong color\n")
            self.assertEqual(note.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                screenshots.save_note(picture, "another")
            with self.assertRaises(ValueError):
                screenshots.save_note(Path(tmp) / "other.png", "x" * (screenshots.MAX_NOTE + 1))

    def test_note_never_follows_a_link(self):
        with TemporaryDirectory() as tmp:
            victim = Path(tmp) / "victim"
            victim.write_text("keep")
            (Path(tmp) / "shot.txt").symlink_to(victim)
            with self.assertRaises(OSError):
                screenshots.save_note(Path(tmp) / "shot.png", "note")
            self.assertEqual(victim.read_text(), "keep")

    def test_default_folder_is_pictures_app_manager(self):
        self.assertEqual(screenshots.default_folder(), Path.home() / "Pictures" / "AppManager")

    def test_layer_origin_is_this_popup_surface(self):
        layers = {
            "DP-1": {"levels": {"2": [{"namespace": "omarchy-bar", "x": 0, "y": 0, "pid": 7}]}},
            "HDMI-A-1": {"levels": {
                "3": [{"namespace": "simple-launcher-for-fools", "x": 5, "y": 5, "pid": 99},
                      {"namespace": "simple-launcher-for-fools", "x": 1920, "y": 30, "pid": 42}]}},
        }
        self.assertEqual(screenshots.layer_origin(layers, pid=42), (1920, 30))
        self.assertIsNone(screenshots.layer_origin(layers, pid=1))
        self.assertIsNone(screenshots.layer_origin(None, pid=42))
        self.assertIsNone(screenshots.layer_origin({"m": {"levels": {"0": [
            {"namespace": "simple-launcher-for-fools", "x": "1", "y": 0, "pid": 42},
            {"namespace": "simple-launcher-for-fools", "x": True, "y": 0, "pid": 42}]}}}, pid=42))
        self.assertIsNone(screenshots.layer_origin({"m": "bogus", "n": {"levels": []}}, pid=42))

    def test_layer_origin_needs_our_pid_and_whole_numbers(self):
        surface = lambda **kw: {"m": {"levels": {"3": [dict(namespace="simple-launcher-for-fools", **kw)]}}}
        self.assertIsNone(screenshots.layer_origin(surface(x=0, y=0), pid=42))            # no pid
        self.assertIsNone(screenshots.layer_origin(surface(x=0, y=0, pid=None), pid=42))
        self.assertIsNone(screenshots.layer_origin(surface(x=0, y=0, pid=True), pid=1))
        for bad in (float("nan"), float("inf"), 1.5):
            self.assertIsNone(screenshots.layer_origin(surface(x=bad, y=0, pid=42), pid=42))
        import json
        layers = json.loads('{"m": {"levels": {"3": [{"namespace": "simple-launcher-for-fools", '
                            '"x": NaN, "y": 0, "pid": 42}]}}}')
        self.assertIsNone(screenshots.layer_origin(layers, pid=42))
        self.assertEqual(screenshots.layer_origin(surface(x=-1920, y=30, pid=42), pid=42), (-1920, 30))

    def test_note_folder_must_be_private_and_not_a_link(self):
        with TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir(mode=0o700)
            link = Path(tmp) / "link"
            link.symlink_to(real)
            with self.assertRaises(OSError):
                screenshots.save_note(link / "shot.png", "note")
            self.assertEqual(list(real.iterdir()), [])
            shared = Path(tmp) / "shared"
            shared.mkdir()
            shared.chmod(0o777)
            with self.assertRaises(ValueError):
                screenshots.save_note(shared / "shot.png", "note")
            self.assertEqual(list(shared.iterdir()), [])

    def test_note_removed_when_it_cannot_be_written(self):
        with TemporaryDirectory() as tmp:
            with patch.object(screenshots.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    screenshots.save_note(Path(tmp) / "shot.png", "note")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_folder_setting_is_checked_when_saved(self):
        import launcher_core
        with TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            (Path(tmp) / "real").mkdir()
            (Path(tmp) / "link").symlink_to(Path(tmp) / "real")
            for bad in ("relative/shots", "/tmp/../etc", "/tmp/bad\nname", str(Path(tmp) / "link" / "x"), 7):
                with self.assertRaises(ValueError):
                    launcher_core.set_screenshot_setting("screenshot_folder", bad, settings)
            self.assertEqual(launcher_core.screenshot_settings(settings), {})
            launcher_core.set_screenshot_setting("screenshot_folder", str(Path(tmp) / "real"), settings)
            launcher_core.set_screenshot_setting("screenshot_key", 0, settings)
            self.assertEqual(launcher_core.screenshot_settings(settings),
                             {"screenshot_key": False, "screenshot_folder": str(Path(tmp) / "real")})
            launcher_core.set_screenshot_setting("screenshot_folder", None, settings)
            self.assertIsNone(launcher_core.screenshot_settings(settings)["screenshot_folder"])
            with self.assertRaises(ValueError):
                launcher_core.set_screenshot_setting("language", "de", settings)

    def test_invalid_geometry_is_refused_before_grim(self):
        with TemporaryDirectory() as tmp, patch.object(screenshots.subprocess, "run") as grim:
            for geometry in ((0, 0, 0, 10), (0, 0, 10, -1), (0, 0, 20000, 10), (True, 0, 10, 10),
                             (0.5, 0, 10, 10), (0, 0, 10), ("0", 0, 10, 10), (0, 0, 10, float("nan")),
                             (1 << 30, 0, 10, 10)):
                with self.assertRaises(ValueError):
                    screenshots.capture(geometry, Path(tmp))
        grim.assert_not_called()
