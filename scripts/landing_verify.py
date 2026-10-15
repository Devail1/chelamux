#!/usr/bin/env python3
"""Does the page chela.pages.dev serves match ``landing/index.html``? (deploy-landing.yml)

Usage: ``landing_verify.py <source.html> <served.html>`` — exit 0 on a match, 1 on a
mismatch, 2 when either file is unreadable or empty (unknown never reads as a match).

Two things the edge does to the page are tolerated, and nothing else:

* Pages rewrites whitespace, so both sides are compared as tokens: every run of spaces and
  newlines collapses to one newline (the same normalisation the workflow's old
  ``tr -s ' \\n' '\\n\\n'`` did).
* Cloudflare Web Analytics (enabled 2026-10-15) injects its beacon ``<script>`` — wrapped
  in ``<!-- Cloudflare Pages Analytics -->`` comments — into the served HTML. That element,
  and only that element, is removed before comparing. Any other difference, down to one
  character of body text, is still a mismatch: that is what catches a PREVIEW deployment
  reporting success while production keeps the old build.
"""

from __future__ import annotations

import re
import sys

_MARKER = r"<!--\s*Cloudflare\s+Pages\s+Analytics\s*-->"
BEACON_RE = re.compile(
    rf"(?:{_MARKER}\s*)?"
    r"<script\b[^>]*\bsrc=(['\"])https://static\.cloudflareinsights\.com/beacon\.min\.js\1"
    r"[^>]*>\s*</script>"
    rf"(?:\s*{_MARKER})?"
)


def strip_beacon(html: str) -> str:
    """Remove every Cloudflare Web Analytics beacon element (and its marker comments)."""
    return BEACON_RE.sub("", html)


def normalise(html: str) -> str:
    return re.sub(r"[ \n]+", "\n", strip_beacon(html))


def matches(source: str, served: str) -> bool:
    return normalise(source) == normalise(served)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return None
    if not text.strip():
        print(f"{path} is empty", file=sys.stderr)
        return None
    return text


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: landing_verify.py <source.html> <served.html>", file=sys.stderr)
        return 2
    source, served = _read(argv[0]), _read(argv[1])
    if source is None or served is None:
        return 2
    if matches(source, served):
        return 0
    want, got = normalise(source).split("\n"), normalise(served).split("\n")
    i = next((k for k, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))
    print(
        f"first difference at token {i}: source {want[i:i + 3]!r} vs served {got[i:i + 3]!r}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
