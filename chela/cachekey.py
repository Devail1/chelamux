"""Window name <-> statusLine cache file key (CMX-30) — ONE mapping, two callers.

The statusLine hook (``scripts/cache-statusline.sh``) caches each agent's payload to
``$CHELA_DIR/context/<key>.json``, and ``chela.context`` reads that file back by the
same window name. A raw window name is not a safe file name: dispatched windows are
named ``<org>/cmx-N-<slug>``, and the ``/`` turned the cache path into a missing
subdirectory, so every dispatched agent and judge silently cached nothing.

Both sides MUST derive the file name through this module — a fix on only one side
just moves the mismatch. The hook runs it as a script (``python3 -I cachekey.py
<window name>`` prints the key), so it stays stdlib-free and import-free: it runs
under whatever ``python3`` the hook finds, with no chela install required.

The encoding is percent-encoding over UTF-8 with a deliberately small safe set, so
it is injective (``decode(encode(n)) == n``), a plain name like ``cmx-12`` keys to
itself, and no key is hidden or a path component (a leading ``.`` is encoded too).
"""

_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")


def encode(name: str) -> str:
    """Filesystem-safe cache key for a window name (no ``/``, no leading ``.``)."""
    out = []
    for i, ch in enumerate(name):
        if ch in _SAFE and not (i == 0 and ch == "."):
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    return "".join(out)


def decode(key: str) -> str:
    """Inverse of :func:`encode` — the window name a cache file's stem was written for."""
    buf = bytearray()
    i = 0
    while i < len(key):
        if key[i] == "%" and _is_hex(key[i + 1:i + 3]):
            buf.append(int(key[i + 1:i + 3], 16))
            i += 3
        else:
            buf.extend(key[i].encode("utf-8"))
            i += 1
    return buf.decode("utf-8", errors="replace")


def _is_hex(s: str) -> bool:
    return len(s) == 2 and all(c in "0123456789abcdefABCDEF" for c in s)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2 or not sys.argv[1]:
        sys.stderr.write("usage: cachekey.py <window name>\n")
        sys.exit(2)
    sys.stdout.write(encode(sys.argv[1]))
