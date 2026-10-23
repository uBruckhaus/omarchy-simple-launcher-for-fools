"""The author's plugins in the Omarchy Plugin Marketplace, for the Information page's cards.

Read from the marketplace catalog (the file the marketplace site and My Plugins use) only when the
Information page is opened, at most once an hour, and only if it changed (its ETag: GitHub answers
"not changed" without the large file). Only the author's own entries are kept, in the cache. Without
the network, the cached list is used, else the built-in one (more_plugins.PLUGINS). A plugin the
app knows keeps its glyph and translated one-liner; a new one gets a generic glyph and the start
of its catalog description.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

import more_plugins

CATALOG_URL = "https://raw.githubusercontent.com/omacom/omarchy-plugin-marketplace/main/site/catalog.json"
OWNER = "ubruckhaus"                       # the repositories' owner on GitHub (the author's plugins)
MAX_AGE = 3600
MAX_BYTES = 40 * 1024 * 1024
MAX_CACHE_BYTES = 1024 * 1024
MAX_PLUGINS = 60
TIMEOUT = 40
CACHE = Path.home() / ".cache/applauncher/plugin-catalog.json"
_ATTEMPTS = {}                             # cache path -> last request, should the cache not be writable
NEW_GLYPH = "\U000F03D6"                   # a package, for a plugin the app has no glyph for
REPO = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/[A-Za-z0-9._-]{1,100}/?$")
ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
ETAG = re.compile(r'^(?:W/)?"[\x21\x23-\x7e]{1,200}"$')


def owner_of(repo) -> str:
    match = REPO.match(repo or "") if isinstance(repo, str) else None
    return match.group(1).lower() if match else ""


def _text(value, limit: int) -> str:
    """Plain text GTK and the cache can take: JSON's escaped lone surrogates ("\\ud800") become "?"."""
    return str(value or "").encode("utf-8", "replace").decode("utf-8")[:limit]


def _entry(plugin_id, name, description="", version="", verified=False):
    """One plugin as kept and shown, or None: the same limits for the network and the cache."""
    if not isinstance(plugin_id, str) or not ID.match(plugin_id) or not isinstance(name, str):
        return None
    name = _text(name.strip(), 60)
    if not name.strip():
        return None
    return {"id": plugin_id, "name": name, "description": _text(description, 600),
            "version": _text(version, 30), "verified": verified is True}


def parse(data) -> list:
    """The author's entries of a catalog: [{id, name, description, version, verified}]."""
    items = data.get("plugins") if isinstance(data, dict) else None
    found = []
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict) or owner_of(entry.get("repo")) != OWNER:
            continue
        kept = _entry(entry.get("id"), entry.get("name"), entry.get("description"), entry.get("version"),
                      entry.get("verificationStatus") == "verified")
        if kept:
            found.append(kept)
            if len(found) >= MAX_PLUGINS:
                break
    return found


def _time(value) -> float:
    """A stored time, or 0 (stale) for anything that is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        number = float(value)          # a huge int (10**400) does not fit
    except OverflowError:
        return 0.0
    return number if math.isfinite(number) else 0.0


def _read_cache(path: Path) -> bytes:
    """The cache file's bytes: a regular file of ours, not a link, FIFO or device, read once and bounded."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("not a regular file of this user")
        chunks, size = [], 0
        while size <= MAX_CACHE_BYTES:
            chunk = os.read(fd, MAX_CACHE_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > MAX_CACHE_BYTES:
            raise OSError("cache too large")
        return b"".join(chunks)
    finally:
        os.close(fd)


def load(path: Path = CACHE) -> dict:
    """The cache, checked like an answer from the network (anyone of this account can edit it)."""
    try:
        data = json.loads(_read_cache(path).decode("utf-8"))
    except (OSError, ValueError, RecursionError):      # deeply nested JSON too
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        return {}
    plugins = []
    for p in data["plugins"]:
        kept = _entry(p.get("id"), p.get("name"), p.get("description"), p.get("version"),
                      p.get("verified")) if isinstance(p, dict) else None
        if kept:
            plugins.append(kept)
            if len(plugins) >= MAX_PLUGINS:
                break
    etag = data.get("etag") if isinstance(data.get("etag"), str) and ETAG.match(data["etag"]) else ""
    return {"plugins": plugins, "etag": etag, "checked": _time(data.get("checked")),
            "tried": _time(data.get("tried"))}


def _save(data: dict, path: Path = CACHE):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plugin-catalog-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def split_response(raw: bytes) -> tuple[int, dict, bytes]:
    """curl --include output: (status, headers of the last response, body)."""
    status, headers, rest = 0, {}, raw
    while rest.startswith(b"HTTP/"):
        head, sep, body = rest.partition(b"\r\n\r\n")
        if not sep:
            break
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        headers = {k.strip().lower(): v.strip() for k, _s, v in (line.partition(":") for line in lines[1:]) if k}
        rest = body
    return status, headers, rest


def curl_command(etag: str = "") -> list:
    """Fixed https address, no redirects, bounded in size and time; the ETag only when it looks like one."""
    argv = ["curl", "--silent", "--include", "--proto", "=https", "--max-redirs", "0",
            "--max-time", str(TIMEOUT), "--max-filesize", str(MAX_BYTES), "--user-agent", "app-manager"]
    if etag and ETAG.match(etag):
        argv += ["--header", f"If-None-Match: {etag}"]
    return argv + ["--", CATALOG_URL]


def fetch(etag: str = "") -> bytes:
    from launcher_core import read_bounded_output
    if not shutil.which("curl"):
        raise OSError("curl is not installed")
    out = read_bounded_output(curl_command(etag), max_bytes=MAX_BYTES + 64 * 1024, timeout=TIMEOUT + 5, binary=True)
    if out is None:
        raise OSError("the marketplace did not answer (or the answer was too large)")
    return out


def refresh(fetch=fetch, path: Path = CACHE, now: float | None = None) -> list:
    """The author's plugins, read again when the last attempt is older than MAX_AGE (blocking: on a
    thread). A failed attempt counts too, so offline or on errors it is tried at most once an hour;
    errors keep the cached list."""
    now = time.time() if now is None else now
    cached = load(path)
    plugins = cached.get("plugins", [])
    last = max(cached.get("checked", 0), cached.get("tried", 0), _ATTEMPTS.get(str(path), 0))
    if last and 0 <= now - last < MAX_AGE:
        return plugins
    _ATTEMPTS[str(path)] = now
    try:
        status, answer, body = split_response(fetch(cached.get("etag", "")))
        if status == 304 and cached:
            pass
        elif status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
                raise ValueError("not a catalog")      # keeps the list read before
            plugins = parse(data)
        else:
            raise OSError(f"HTTP {status}")
        etag = answer.get("etag", "")
        record = {"checked": now, "etag": etag if ETAG.match(etag) else "", "plugins": plugins}
    except Exception:          # offline, too large, refused, not JSON: the cached list (or none)
        record = {**cached, "plugins": plugins, "tried": now}
    try:
        _save(record, path)
    except OSError:
        pass
    return plugins


def first_sentence(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    cut = re.split(r"(?<=[.!?:])\s", text, maxsplit=1)[0]
    return cut if len(cut) <= limit else cut[:limit - 1].rsplit(" ", 1)[0] + "…"


def cards(plugins: list) -> list:
    """(id, name, glyph, text) for more_plugins.build_cards: known plugins in the built-in order
    with their glyph and one-liner (translated there), then new ones from the catalog."""
    if not plugins:
        return list(more_plugins.PLUGINS)
    known = {p[0]: p for p in more_plugins.PLUGINS}
    listed = {p["id"]: p for p in plugins}
    out = [(pid, listed[pid]["name"] or name, glyph, text) for pid, name, glyph, text in more_plugins.PLUGINS
           if pid in listed]
    out += [(p["id"], p["name"], NEW_GLYPH, first_sentence(p.get("description", "")) or p["name"])
            for p in plugins if p["id"] not in known]
    return out
