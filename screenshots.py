"""Capture the App Manager popup with F12, without shell interpolation.

Ported from Theme Manager: grim writes into a new private folder, the
finished picture is then copied into the chosen folder through a handle on
that folder (private, no link followed, named only once complete). Nothing
is sent anywhere; a file is only written when F12 is pressed."""
from __future__ import annotations

import os
import secrets
import stat
import subprocess
import sys
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


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _closed_chain(target: Path) -> bool:
    """Every folder from / down to `target` (a resolved path, no links) is
    yours or root's, and no other account can change it: not writable by
    group or others, except a root-owned sticky folder like /tmp (others
    cannot rename or remove what is yours there)."""
    current = Path("/")
    for part in (Path("/"),) + tuple(Path(p) for p in target.parts[1:]):
        current = current / part if part != Path("/") else current
        try:
            info = current.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
        if info.st_uid not in (0, os.getuid()):
            return False
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            return False
    return True


def _pictures() -> Path:
    """~/Pictures as a path without links. Only your own ~/Pictures link is
    followed (Pictures on another disk), and only when the home folder is
    closed to other accounts (so nobody else can swap the link), the link
    text has no `..` and its target, and every folder down to it, is a plain
    folder closed to other accounts (_closed_chain refuses any link on the
    way). The link is read through a handle on the home folder, so it is the
    very link that was checked; AppManager below it is then reached with the
    no-follow walk."""
    home = Path(os.path.realpath(Path.home()))
    pictures = home / "Pictures"
    home_fd = os.open(home, _DIR_FLAGS)
    try:
        try:
            info = os.stat("Pictures", dir_fd=home_fd, follow_symlinks=False)
        except FileNotFoundError:
            return pictures                   # made (0700) by the walk
        if not stat.S_ISLNK(info.st_mode):
            return pictures                   # a folder: walked without following links
        home_info = os.fstat(home_fd)
        if home_info.st_uid != os.getuid() or home_info.st_mode & 0o022 or not _closed_chain(home):
            raise ValueError("your home folder can be changed by other accounts: the ~/Pictures link is not followed")
        if info.st_uid != os.getuid():
            raise ValueError("~/Pictures is a link that is not yours")
        link = os.readlink("Pictures", dir_fd=home_fd)
    finally:
        os.close(home_fd)
    if ".." in Path(link).parts:
        raise ValueError("the ~/Pictures link must not contain ..")
    target = Path(os.path.normpath(os.path.join(home, link)))
    if not _closed_chain(target):
        raise ValueError("~/Pictures points to a folder other accounts can change, or through another link")
    return target


def _destination(folder: Path | None) -> Path:
    """The folder to walk: the default one under _pictures(); a custom folder
    as given (valid_folder_setting already refused links in it)."""
    default = default_folder()
    if folder is None or folder == default:
        if default.parent == Path.home() / "Pictures":
            return _pictures() / default.name
        return default
    return folder


def _creatable_roots() -> tuple:
    """Where missing folders may be made: inside your home, and inside the
    checked target of your own ~/Pictures link."""
    home = Path(os.path.realpath(Path.home()))
    try:
        return (home, _pictures())
    except ValueError:
        return (home,)


def open_folder(folder: Path, create: bool = False) -> int:
    """A handle on `folder`, reached component by component from / with
    O_DIRECTORY|O_NOFOLLOW, so no link anywhere on the path is followed, also
    one swapped in after the setting was checked. Missing folders are created
    (0700) only inside your home folder (or ~/Pictures' real place), and only
    when `create` is set. The
    folder itself must be yours and not writable by others."""
    folder = Path(folder)
    if not folder.is_absolute() or any(part in (".", "..") for part in folder.parts):
        raise ValueError("the screenshot folder must be an absolute path")
    roots = _creatable_roots() if create else ()
    fd = os.open("/", _DIR_FLAGS)
    try:
        current = Path("/")
        for part in folder.parts[1:]:
            current /= part
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not any(current != root and current.is_relative_to(root) for root in roots):
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass                       # made meanwhile: opened below, links still refused
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("the screenshot folder must be yours and not writable by others")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_all(fd: int, data: bytes):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _publish(dir_fd: int, fill, names) -> str:
    """Write a new file in the checked folder and give it the first free name
    of `names`. It is written and synced under a temporary hidden name
    (exclusive, 0600, no link followed) and only then hard-linked to its name
    (link never replaces an existing file); the temporary name is removed
    afterwards and on any failure. A cut-off file can so never carry a final
    name, even if removing it fails. FileExistsError: every name was taken."""
    for _attempt in range(100):
        temporary = f".app-manager-{secrets.token_hex(8)}.part"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=dir_fd)
            break
        except FileExistsError:
            continue
    else:
        raise OSError("no free temporary name")
    try:
        try:
            fill(fd)
            os.fsync(fd)
        finally:
            os.close(fd)
        for name in names:
            try:
                os.link(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
                return name
            except FileExistsError:
                continue
        raise FileExistsError("no free name")
    finally:
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        except OSError as error:
            # It never carries a picture's or note's name; say it is there.
            print(f"App Manager: could not remove the temporary screenshot file {temporary}: {error}",
                  file=sys.stderr)


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
    The folder must be yours and closed to others and is reached without
    following any link; the picture gets its name only when complete
    (_publish), never replacing an existing picture or taking a note's name."""
    if not isinstance(geometry, (tuple, list)) or len(geometry) != 4 or \
            not all(isinstance(v, int) and not isinstance(v, bool) for v in geometry):
        raise ValueError("invalid screenshot geometry")
    x, y, width, height = geometry
    if abs(x) > 1 << 20 or abs(y) > 1 << 20 or width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ValueError("invalid screenshot geometry")
    folder = _destination(folder)
    dir_fd = open_folder(folder, create=True)
    try:
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
                stem = f"app-manager-{stamp}"

                def names():
                    for suffix in range(1, 10000):
                        name = f"{stem}.png" if suffix == 1 else f"{stem}-{suffix}.png"
                        if not _taken(dir_fd, name[:-4] + ".txt"):
                            yield name

                def fill(target):
                    # Every byte written (short writes continued): the whole
                    # picture, or it never gets its name.
                    copied = 0
                    while copied < size:
                        chunk = os.read(source, min(1024 * 1024, size - copied))
                        if not chunk:
                            raise OSError("the screenshot ended early")
                        _write_all(target, chunk)
                        copied += len(chunk)
                    if os.read(source, 1):
                        raise OSError("the screenshot changed while it was copied")
                return folder / _publish(dir_fd, fill, names())
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
    dir_fd = open_folder(path.parent)            # the same walk: no link on the way
    try:
        data = (note + "\n").encode("utf-8")
        _publish(dir_fd, lambda fd: _write_all(fd, data), [path.name])
    finally:
        os.close(dir_fd)
    return path
