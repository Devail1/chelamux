"""Owner-only store of the LIVE shares, so a share survives a restart of the process
hosting its bridge (CMX-434).

One JSON file, ``$CHELA_DIR/shares.json``, mode 0600, written atomically (temp file +
rename). It holds each live share's pairing SECRET — the capability a guest pastes — so:

  * it is created 0600 and re-chmodded on every write, never wider;
  * it refuses to live inside a git work tree (a ``$CHELA_DIR`` pointed into a checkout
    would otherwise put a guest capability one ``git add -A`` away from a public repo);
  * a share leaves the store the moment it is deliberately stopped (``Bridge.stop``), and
    stays only across a process exit (``collab_stream.shutdown_all``).

Record shape (one per wid, all JSON-native)::

    {"wid", "secret" (hex), "room", "allow_typing", "window_key",
     "override": None | {"expires_at", "started_at", "granted_by", "window", "joiner"},
     "share_epoch", "seq_ceiling"}

``seq_ceiling`` is the AES-GCM nonce guard: the bridge never seals a host frame with a
seq at or past the persisted ceiling, so a restored bridge resuming AT the ceiling can
never reuse a (key, nonce) pair (see ``collab_stream.Bridge._reserve_seq``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

STORE_NAME = "shares.json"
_lock = threading.Lock()


class UnsafeStoreLocation(RuntimeError):
    """The store would land inside a git work tree — refused, never written."""


def path() -> Path:
    return Path(config.CHELA_DIR) / STORE_NAME


def inside_repo(p: Path) -> bool:
    """True when ``p`` sits inside a git work tree (any ancestor holds a ``.git``)."""
    try:
        cur = p.resolve().parent
    except OSError:
        cur = p.parent
    for d in (cur, *cur.parents):
        if (d / ".git").exists():
            return True
    return False


def _checked_path() -> Path:
    p = path()
    if inside_repo(p):
        raise UnsafeStoreLocation(f"refusing to keep share secrets inside a git work tree: {p}")
    return p


def load() -> dict[str, dict]:
    """wid → record. Empty on no file, an unreadable file, or an unsafe location."""
    try:
        p = _checked_path()
    except UnsafeStoreLocation as e:
        log.warning("share_store: %s", e)
        return {}
    with _lock:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("share_store: unreadable %s (%s) — no share restored", p, e)
            return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _write(records: dict[str, dict]) -> None:
    p = _checked_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".shares.", dir=str(p.parent))   # mkstemp is 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        os.chmod(p, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def put(wid: str, record: dict) -> None:
    """Insert or replace one share. Raises on failure — a caller that relies on the record
    (the seq ceiling) must know it did not land."""
    with _lock:
        records = _load_unlocked()
        records[wid] = record
        _write(records)


def drop(wid: str) -> None:
    """Forget one share (it was deliberately stopped). Best-effort, never raises."""
    try:
        with _lock:
            records = _load_unlocked()
            if records.pop(wid, None) is None:
                return
            if records:
                _write(records)
            else:
                path().unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log.warning("share_store: could not drop %s: %s", wid, e)


def _load_unlocked() -> dict[str, dict]:
    try:
        with open(_checked_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def count() -> int:
    return len(load())
