"""📎 Saving a file dropped on a Wall terminal into that session's workspace (CMX-412).

The browser cannot hand a pane a host path — the file is on the viewer's device (a phone,
another laptop). So the dashboard receives the bytes and writes them to
``<session cwd>/uploads/<name>``; the caller then types ``@uploads/<name>`` into the pane.

Everything here is about the one thing a write into an agent's workspace must never do:
land somewhere else. The rules, each refused rather than "fixed up":

* a name with a path separator, a ``..``, a leading dot, a NUL or nothing left after
  cleaning is refused — it is never re-interpreted into a different name;
* ``uploads/`` itself must be a real directory directly under the cwd — a symlinked
  ``uploads/`` (pointing anywhere) is refused, as is a final path that resolves outside it;
* an existing file is never overwritten: the name gets ``-1``, ``-2``… and the write uses
  ``O_EXCL | O_NOFOLLOW``, so a racing writer or a planted symlink cannot be written through;
* over the size cap ⇒ refused and nothing is left on disk.

Pure filesystem code: no Flask, no tmux — the dashboard route wraps it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import BinaryIO

UPLOADS_DIRNAME = "uploads"
_MAX_NAME = 120
_MAX_SUFFIX = 1000
_CHUNK = 1024 * 1024
# Anything outside this set is replaced by "_" — the name is typed into a prompt as
# ``@uploads/<name>``, so a space or shell/markdown metacharacter would break the mention.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class UploadRefused(Exception):
    """A refusal with a short machine ``reason`` and a human ``message``."""

    def __init__(self, reason: str, message: str, status: int = 400):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status


def sanitise_name(raw: str) -> str:
    """The on-disk name for a browser-supplied filename, or :class:`UploadRefused`.

    Separators, ``..`` and dotfiles are REFUSED (not stripped): a name that tries to
    climb or hide is not an honest filename, and silently rewriting it would save the
    file under a name the sender never chose."""
    name = (raw or "").strip()
    if not name:
        raise UploadRefused("bad_name", "The file has no name.")
    if "/" in name or "\\" in name or "\0" in name:
        raise UploadRefused("bad_name", "File names may not contain a path separator.")
    cleaned = _UNSAFE.sub("_", name).strip("_")
    if not cleaned:
        raise UploadRefused("bad_name", "The file name has no usable characters.")
    # With separators already refused, a leading dot covers both `..` and dotfiles — checked
    # AFTER cleaning, which can expose one ("_.env" → ".env").
    if cleaned.startswith("."):
        raise UploadRefused("bad_name", "Hidden (dot) files and '..' are not accepted.")
    if len(cleaned) > _MAX_NAME:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and 0 < len(ext) <= 16:
            cleaned = stem[: _MAX_NAME - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:_MAX_NAME]
    return cleaned


def uploads_dir(cwd: str | os.PathLike) -> Path:
    """``<cwd>/uploads`` as a real directory, created if missing — or :class:`UploadRefused`
    when it is a symlink or anything but a directory directly under the resolved cwd."""
    root = Path(cwd)
    if not root.is_absolute() or not root.is_dir():
        raise UploadRefused("no_workspace", "The session has no usable working directory.", 409)
    root = root.resolve()
    up = root / UPLOADS_DIRNAME
    try:
        up.mkdir(mode=0o755)
    except FileExistsError:
        pass                      # already there — or a symlink / file, refused just below
    except OSError as e:
        raise UploadRefused("write_failed", f"Could not create uploads/: {e}", 500) from e
    # A symlinked uploads/ is refused wherever it points (even inside the workspace).
    if up.is_symlink() or not up.is_dir() or up.resolve() != up:
        raise UploadRefused("outside_uploads",
                            "uploads/ is not a plain directory in the workspace — refusing to write.", 403)
    return up


def _contained(path: Path, up: Path) -> bool:
    """True only when ``path`` resolves to a direct child of ``up`` (itself resolved)."""
    return path.resolve().parent == up.resolve()


def _candidates(name: str):
    yield name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    for n in range(1, _MAX_SUFFIX + 1):
        yield f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"


def save(cwd: str | os.PathLike, raw_name: str, stream: BinaryIO, max_bytes: int) -> tuple[str, int]:
    """Write ``stream`` to ``<cwd>/uploads/<name>`` → ``(saved_name, size)``.

    Never overwrites (suffixes instead), refuses anything that would land outside
    ``uploads/``, and removes the partial file if the stream runs over ``max_bytes``."""
    name = sanitise_name(raw_name)
    up = uploads_dir(cwd)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for cand in _candidates(name):
        target = up / cand
        if not _contained(target, up):
            raise UploadRefused("outside_uploads", "The file would land outside uploads/.", 403)
        try:
            fd = os.open(target, flags, 0o644)
        except FileExistsError:
            continue
        except OSError as e:
            raise UploadRefused("write_failed", f"Could not write the file: {e}", 500) from e
        size = 0
        ok = False
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = stream.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadRefused(
                            "too_large",
                            f"File is over the {max_bytes // (1024 * 1024)} MB upload limit.", 413)
                    out.write(chunk)
            ok = True
        finally:
            if not ok:
                try:
                    target.unlink()
                except OSError:
                    pass
        return cand, size
    raise UploadRefused("name_taken", "Too many files with that name in uploads/.", 409)
