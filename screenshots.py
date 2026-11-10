"""Capture the App Manager popup with F12, without shell interpolation.

Ported from Theme Manager: grim writes into a new private folder, the
finished picture is then copied into the chosen folder through a handle on
that folder (created exclusively, private, never following a link). Nothing
is sent anywhere; a file is only written when F12 is pressed."""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

NAMESPACE = "simple-launcher-for-fools"   # the popup's layer-shell namespace
MAX_SHOT = 256 * 1024 * 1024              # a PNG of the popup is far smaller
MAX_NOTE = 1000


def default_folder() -> Path:
    return Path.home() / "Pictures" / "AppManager"


def key_enabled(settings: dict) -> bool:
    return settings.get("screenshot_key") is not False   # on unless turned off


def valid_folder_setting(value) -> bool:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 4096 or \
            any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if any(part in (".", "..") for part in value.split("/")):
        return False
    current = Path("/")
    for part in Path(value).parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    return True


def folder_from_settings(settings: dict) -> Path:
    value = settings.get("screenshot_folder")
    return Path(value) if valid_folder_setting(value) else default_folder()


def layer_origin(layers, namespace: str = NAMESPACE, pid: int | None = None):
    """Where this popup's full-screen layer surface starts on screen (global
    logical pixels), from `hyprctl layers -j`; None when it is not mapped.
    The surface leaves out the bar's edge, so it may not start at the
    monitor's corner."""
    if not isinstance(layers, dict):
        return None
    pid = os.getpid() if pid is None else pid
    for monitor in layers.values():
        levels = monitor.get("levels", {}) if isinstance(monitor, dict) else {}
        for surfaces in levels.values() if isinstance(levels, dict) else ():
            for layer in surfaces if isinstance(surfaces, list) else ():
                if not isinstance(layer, dict) or layer.get("namespace") != namespace:
                    continue
                if layer.get("pid") != pid or isinstance(layer.get("pid"), bool):
                    continue                  # only this process's own surface
                x, y = layer.get("x"), layer.get("y")
                if isinstance(x, int) and isinstance(y, int) and not isinstance(x, bool) \
                        and not isinstance(y, bool):
                    return x, y
    return None


def _private_dir() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = runtime if runtime and os.path.isdir(runtime) else None
    return tempfile.mkdtemp(prefix="app-manager-shot-", dir=base)     # 0700, ours


def _taken(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def capture(geometry: tuple[int, int, int, int], folder: Path | None = None,
            now: datetime | None = None) -> Path:
    """Save one PNG of the screen rectangle `geometry` (x, y, width, height).
    The folder must be yours and closed to others; the name is created
    exclusively, never follows a link and never takes the name of an
    existing picture or note."""
    if not isinstance(geometry, (tuple, list)) or len(geometry) != 4 or \
            not all(isinstance(v, int) and not isinstance(v, bool) for v in geometry):
        raise ValueError("invalid screenshot geometry")
    x, y, width, height = geometry
    if abs(x) > 1 << 20 or abs(y) > 1 << 20 or width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ValueError("invalid screenshot geometry")
    folder = folder or default_folder()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    dir_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(dir_fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("the screenshot folder must be yours and not writable by others")
        private = _private_dir()
        shot = os.path.join(private, "shot.png")
        try:
            result = subprocess.run(["grim", "-g", f"{x},{y} {width}x{height}", shot],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=15, check=False)
            if result.returncode != 0:
                raise OSError("grim failed")
            source = os.open(shot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                size = os.fstat(source).st_size
                if size == 0 or size > MAX_SHOT:
                    raise OSError("grim produced no usable screenshot")
                stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H-%M-%S")
                stem, suffix = f"app-manager-{stamp}", 1
                while True:
                    name = f"{stem}.png" if suffix == 1 else f"{stem}-{suffix}.png"
                    suffix += 1
                    if suffix > 10000:
                        raise OSError("no free screenshot name")
                    if _taken(dir_fd, name[:-4] + ".txt"):
                        continue
                    try:
                        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                         | os.O_CLOEXEC, 0o600, dir_fd=dir_fd)
                        break
                    except FileExistsError:
                        continue
                try:
                    copied = 0
                    while copied < size:
                        chunk = os.read(source, min(1024 * 1024, size - copied))
                        if not chunk:
                            break
                        os.write(target, chunk)
                        copied += len(chunk)
                finally:
                    os.close(target)
                return folder / name
            finally:
                os.close(source)
        finally:
            for entry in os.listdir(private):
                os.unlink(os.path.join(private, entry))
            os.rmdir(private)
    finally:
        os.close(dir_fd)


def capture_to_settings(geometry: tuple[int, int, int, int], settings: dict) -> Path:
    """A custom folder that cannot be written falls back to the default."""
    folder = folder_from_settings(settings)
    try:
        return capture(geometry, folder)
    except (OSError, ValueError):
        if folder == default_folder():
            raise
        return capture(geometry, default_folder())


def save_note(picture: Path, note: str) -> Path | None:
    """Write an optional short note beside the screenshot, never replacing one.
    No note typed: no file."""
    note = note.strip()
    if not note:
        return None
    if len(note) > MAX_NOTE:
        raise ValueError("screenshot note is too long")
    path = picture.with_suffix(".txt")
    # As capture(): through a handle on the folder, which must be yours and
    # closed to others; the note is created exclusively, never via a link,
    # and removed again if it cannot be written in full.
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(dir_fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("the screenshot folder must be yours and not writable by others")
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dir_fd)
        try:
            data = (note + "\n").encode("utf-8")
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            fd = None
            try:
                os.unlink(path.name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        os.close(dir_fd)
    return path
