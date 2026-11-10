"""F12 screenshot paths and capture failures, without a compositor."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import screenshots  # noqa: E402


_HOME = None


def setUpModule():
    """No test may ever write into the real home: Path.home() is a scratch folder."""
    global _HOME
    _HOME = TemporaryDirectory()
    home = Path(_HOME.name) / "home"
    home.mkdir(mode=0o700)
    setUpModule.patch = patch.object(screenshots.Path, "home", return_value=home)
    setUpModule.patch.start()


def tearDownModule():
    setUpModule.patch.stop()
    _HOME.cleanup()


def fake_grim(data=b"PNG-DATA-0123456789"):
    def run(argv, **_kwargs):
        Path(argv[-1]).write_bytes(data)
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    return run


class HostileFolderTests(TestCase):
    """Marketplace review 2.5.0: short writes, partial files, links on the way."""

    def test_short_writes_are_continued(self):
        real_write = os.write
        with TemporaryDirectory() as tmp, \
                patch.object(screenshots.subprocess, "run", side_effect=fake_grim(b"x" * 5000)), \
                patch.object(screenshots.os, "write", side_effect=lambda fd, data: real_write(fd, bytes(data[:7]))):
            path = screenshots.capture((0, 0, 10, 10), Path(tmp))
            self.assertEqual(path.read_bytes(), b"x" * 5000)

    def test_failed_copy_leaves_no_partial_picture(self):
        for failing in ("fsync", "write"):
            with TemporaryDirectory() as tmp, \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()), \
                    patch.object(screenshots.os, failing, side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    screenshots.capture((0, 0, 10, 10), Path(tmp))
                self.assertEqual(list(Path(tmp).iterdir()), [], failing)

    def test_early_end_of_the_picture_is_a_failure(self):
        real_read = os.read
        calls = []

        def short_read(fd, n):
            calls.append(n)
            return real_read(fd, n) if len(calls) == 1 else b""
        with TemporaryDirectory() as tmp, \
                patch.object(screenshots.subprocess, "run", side_effect=fake_grim(b"y" * 3 * 1024 * 1024)), \
                patch.object(screenshots.os, "read", side_effect=short_read):
            with self.assertRaises(OSError):
                screenshots.capture((0, 0, 10, 10), Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_custom_folder_failure_leaves_nothing_there_and_saves_to_default(self):
        with TemporaryDirectory() as tmp:
            custom, default = Path(tmp) / "custom", Path(tmp) / "default"
            custom.mkdir(mode=0o700)
            default.mkdir(mode=0o700)
            real_fsync, seen = os.fsync, []

            def fsync_once(fd):
                seen.append(fd)
                if len(seen) == 1:
                    raise OSError("disk full")
                return real_fsync(fd)
            with patch.object(screenshots, "default_folder", return_value=default), \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()), \
                    patch.object(screenshots.os, "fsync", side_effect=fsync_once):
                path = screenshots.capture_to_settings((0, 0, 10, 10), {"screenshot_folder": str(custom)})
            self.assertEqual(list(custom.iterdir()), [])
            self.assertEqual(path.parent, Path(os.path.realpath(default)))
            self.assertEqual(path.read_bytes(), b"PNG-DATA-0123456789")

    def test_a_link_swapped_into_the_path_is_never_followed(self):
        # The setting was checked while "a" was a folder; it is a link now.
        with TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            (outside / "b").mkdir(parents=True, mode=0o700)
            (Path(tmp) / "a").symlink_to(outside)
            folder = Path(tmp) / "a" / "b"
            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim()) as grim:
                with self.assertRaises(OSError):
                    screenshots.capture((0, 0, 10, 10), folder)
            grim.assert_not_called()
            with self.assertRaises(OSError):
                screenshots.save_note(folder / "shot.png", "note")
            self.assertEqual(list((outside / "b").iterdir()), [])

    def test_missing_folders_are_made_only_inside_home(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir(mode=0o700)
            with patch.object(screenshots.Path, "home", return_value=home), \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()):
                path = screenshots.capture((0, 0, 10, 10), home / "Pictures" / "Shots")
                self.assertEqual(path.parent, home / "Pictures" / "Shots")
                self.assertEqual((home / "Pictures").stat().st_mode & 0o777, 0o700)
                with self.assertRaises(FileNotFoundError):
                    screenshots.capture((0, 0, 10, 10), Path(tmp) / "elsewhere" / "Shots")
            self.assertFalse((Path(tmp) / "elsewhere").exists())

    def test_pictures_link_into_a_folder_others_can_change_is_refused(self):
        with TemporaryDirectory() as tmp:
            home, shared = Path(tmp) / "home", Path(tmp) / "shared"
            home.mkdir(mode=0o700)
            shared.mkdir()
            shared.chmod(0o777)                       # not sticky: others could plant links here
            (home / "Pictures").symlink_to(shared)
            with patch.object(screenshots.Path, "home", return_value=home), \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()) as grim:
                with self.assertRaises(ValueError):
                    screenshots.capture((0, 0, 10, 10))
                with self.assertRaises(ValueError):
                    screenshots.capture_to_settings((0, 0, 10, 10), {})
                self.assertEqual(screenshots._creatable_roots(), (Path(os.path.realpath(home)),))
            grim.assert_not_called()
            self.assertEqual(list(shared.iterdir()), [])

    def test_appmanager_link_planted_under_pictures_is_not_followed(self):
        with TemporaryDirectory() as tmp:
            home, disk, victim = Path(tmp) / "home", Path(tmp) / "disk", Path(tmp) / "victim"
            home.mkdir(mode=0o700)
            disk.mkdir(mode=0o700)
            victim.mkdir(mode=0o700)
            (home / "Pictures").symlink_to(disk)
            (disk / "AppManager").symlink_to(victim)
            with patch.object(screenshots.Path, "home", return_value=home), \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()) as grim:
                with self.assertRaises(OSError):
                    screenshots.capture((0, 0, 10, 10))
            grim.assert_not_called()
            self.assertEqual(list(victim.iterdir()), [])

    def test_closed_chain(self):
        with TemporaryDirectory() as tmp:
            ok = Path(os.path.realpath(tmp)) / "ok"
            ok.mkdir(mode=0o700)
            self.assertTrue(screenshots._closed_chain(ok))     # /tmp: root's and sticky
            open_dir = ok / "open"
            open_dir.mkdir()
            open_dir.chmod(0o775)                              # the group may change it
            self.assertFalse(screenshots._closed_chain(open_dir))
            self.assertFalse(screenshots._closed_chain(ok / "missing"))

    def test_cut_off_picture_never_gets_its_name_even_if_cleanup_fails(self):
        with TemporaryDirectory() as tmp, \
                patch.object(screenshots.subprocess, "run", side_effect=fake_grim()), \
                patch.object(screenshots.os, "fsync", side_effect=OSError("disk full")), \
                patch.object(screenshots.os, "unlink", side_effect=OSError("cannot remove")), \
                patch.object(screenshots.sys, "stderr", new=__import__("io").StringIO()):
            with self.assertRaises(OSError):
                screenshots.capture((0, 0, 10, 10), Path(tmp), datetime(2026, 11, 10, 9, 0, 0))
            left = [p.name for p in Path(tmp).iterdir()]
            self.assertFalse([n for n in left if not n.startswith(".app-manager-") or not n.endswith(".part")], left)
            self.assertFalse((Path(tmp) / "app-manager-2026-11-10-09-00-00.png").exists())

    def test_picture_gets_its_name_only_when_complete_and_never_replaces(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            now = datetime(2026, 11, 10, 9, 0, 0)
            (folder / "app-manager-2026-11-10-09-00-00.png").write_bytes(b"old")
            with patch.object(screenshots.subprocess, "run", side_effect=fake_grim(b"new")):
                path = screenshots.capture((0, 0, 10, 10), folder, now)
            self.assertEqual(path.name, "app-manager-2026-11-10-09-00-00-2.png")
            self.assertEqual((folder / "app-manager-2026-11-10-09-00-00.png").read_bytes(), b"old")
            self.assertEqual(path.stat().st_nlink, 1)              # the temporary name is gone
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             ["app-manager-2026-11-10-09-00-00-2.png", "app-manager-2026-11-10-09-00-00.png"])

    def test_note_never_replaces_or_follows_and_fails_without_its_name(self):
        with TemporaryDirectory() as tmp:
            folder, victim = Path(tmp), Path(tmp) / "victim"
            victim.write_text("keep")
            (folder / "a.txt").symlink_to(victim)
            with self.assertRaises(FileExistsError):
                screenshots.save_note(folder / "a.png", "note")
            self.assertEqual(victim.read_text(), "keep")
            with patch.object(screenshots.os, "fsync", side_effect=OSError("disk full")), \
                    patch.object(screenshots.os, "unlink", side_effect=OSError("cannot remove")), \
                    patch.object(screenshots.sys, "stderr", new=__import__("io").StringIO()):
                with self.assertRaises(OSError):
                    screenshots.save_note(folder / "b.png", "note")
            self.assertFalse((folder / "b.txt").exists())

    def make_home(self, tmp, mode=0o700):
        home, disk = Path(tmp) / "home", Path(tmp) / "disk"
        home.mkdir()
        home.chmod(mode)
        disk.mkdir(mode=0o700)
        return home, disk

    def test_pictures_link_not_followed_in_a_home_others_can_change(self):
        for mode in (0o777, 0o770, 0o1777):
            with TemporaryDirectory() as tmp:
                home, disk = self.make_home(tmp, mode)
                (home / "Pictures").symlink_to(disk)
                with patch.object(screenshots.Path, "home", return_value=home), \
                        patch.object(screenshots.subprocess, "run", side_effect=fake_grim()) as grim:
                    with self.assertRaises(ValueError):
                        screenshots.capture((0, 0, 10, 10))
                grim.assert_not_called()
                self.assertEqual(list(disk.iterdir()), [], oct(mode))

    def test_plain_pictures_folder_needs_no_link_check(self):
        with TemporaryDirectory() as tmp:
            home, _disk = self.make_home(tmp)
            with patch.object(screenshots.Path, "home", return_value=home), \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()):
                path = screenshots.capture((0, 0, 10, 10))
            self.assertEqual(path.parent, Path(os.path.realpath(home)) / "Pictures" / "AppManager")

    def test_pictures_link_through_another_link_or_dotdot_is_refused(self):
        with TemporaryDirectory() as tmp:
            home, disk = self.make_home(tmp)
            (disk / "pics").mkdir(mode=0o700)
            (Path(tmp) / "via").symlink_to(disk)
            for text in (str(Path(tmp) / "via" / "pics"), "../disk/pics"):
                (home / "Pictures").unlink(missing_ok=True)
                (home / "Pictures").symlink_to(text)
                with patch.object(screenshots.Path, "home", return_value=home), \
                        patch.object(screenshots.subprocess, "run", side_effect=fake_grim()) as grim:
                    with self.assertRaises(ValueError):
                        screenshots.capture((0, 0, 10, 10))
                grim.assert_not_called()
            self.assertEqual(list((disk / "pics").iterdir()), [])

    def test_the_link_read_is_the_one_in_the_checked_home(self):
        with TemporaryDirectory() as tmp:
            home, disk = self.make_home(tmp)
            (home / "Pictures").symlink_to(disk)
            calls, real_readlink = [], os.readlink

            def spy(path, *, dir_fd=None):
                calls.append((path, dir_fd))
                return real_readlink(path, dir_fd=dir_fd)
            with patch.object(screenshots.Path, "home", return_value=home), \
                    patch.object(screenshots.os, "readlink", side_effect=spy):
                self.assertEqual(screenshots._pictures(), Path(os.path.realpath(disk)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "Pictures")         # relative to the opened home
            self.assertIsInstance(calls[0][1], int)

    def test_a_temporary_file_that_cannot_be_removed_is_reported(self):
        import io
        err = io.StringIO()
        with TemporaryDirectory() as tmp, \
                patch.object(screenshots.subprocess, "run", side_effect=fake_grim()), \
                patch.object(screenshots.os, "fsync", side_effect=OSError("disk full")), \
                patch.object(screenshots.os, "unlink", side_effect=PermissionError("cannot remove")), \
                patch.object(screenshots.sys, "stderr", err):
            with self.assertRaises(OSError):
                screenshots.capture((0, 0, 10, 10), Path(tmp))
        self.assertIn("could not remove the temporary screenshot file .app-manager-", err.getvalue())

    def test_default_folder_behind_your_own_link_still_works(self):
        with TemporaryDirectory() as tmp:
            home, disk = Path(tmp) / "home", Path(tmp) / "disk"
            home.mkdir(mode=0o700)
            (disk / "Pictures").mkdir(parents=True, mode=0o700)
            (home / "Pictures").symlink_to(disk / "Pictures")
            with patch.object(screenshots.Path, "home", return_value=home), \
                    patch.object(screenshots.subprocess, "run", side_effect=fake_grim()):
                path = screenshots.capture((0, 0, 10, 10))
            # Resolved once (the link is yours); below it nothing is followed.
            self.assertEqual(path.parent, Path(os.path.realpath(disk / "Pictures" / "AppManager")))
            self.assertEqual((disk / "Pictures" / "AppManager").stat().st_mode & 0o777, 0o700)


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
