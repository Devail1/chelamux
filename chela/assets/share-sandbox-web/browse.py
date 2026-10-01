#!/usr/bin/env python3
"""browse — read a web page with the sandbox's headless Chromium.

    browse <url> [--links] [--html] [--max-chars N] [--wait-ms N] [--screenshot PATH]

Prints the final URL, HTTP status and title, then the page's visible text (and, with
--links, every link as `text -> href`). Chromium runs headless, with a throwaway profile
under /tmp, and reaches the network ONLY through the session's egress proxy
($HTTPS_PROXY) — which refuses private / LAN / host addresses and rate-limits.

`--no-sandbox` (Playwright's chromium_sandbox=False): Chromium's own sandbox needs user
namespaces or a setuid helper, i.e. CAP_SYS_ADMIN or a loosened seccomp profile on the
container. Granting either would weaken the OUTER boundary (the container) to strengthen
an inner one, so we don't: a renderer compromise lands in a container that already lets
the guest's Claude run arbitrary code, with the same workspace, network and caps.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="browse", description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("--links", action="store_true", help="also list the page's links")
    ap.add_argument("--html", action="store_true", help="print the HTML instead of the text")
    ap.add_argument("--max-chars", type=int, default=20000)
    ap.add_argument("--wait-ms", type=int, default=1500, help="extra settle time after load")
    ap.add_argument("--screenshot", metavar="PATH", help="also save a full-page PNG")
    a = ap.parse_args(argv)

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        print("browse: no HTTPS_PROXY — this session has no web access", file=sys.stderr)
        return 2
    url = a.url if "://" in a.url else "https://" + a.url

    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, chromium_sandbox=False,
                                    proxy={"server": proxy},
                                    args=["--disable-dev-shm-usage"])
        try:
            page = browser.new_context(service_workers="block").new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Error as e:
                print(f"browse: could not load {url}: {e.message.splitlines()[0]}", file=sys.stderr)
                return 1
            page.wait_for_timeout(a.wait_ms)
            print(f"URL: {page.url}")
            print(f"Status: {resp.status if resp else '?'}")
            print(f"Title: {page.title()}")
            print()
            body = page.content() if a.html else page.inner_text("body")
            if len(body) > a.max_chars:
                body = body[:a.max_chars] + f"\n… [truncated at {a.max_chars} chars]"
            print(body)
            if a.links:
                print("\nLinks:")
                for text, href in page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => [e.innerText.trim(), e.href])"):
                    print(f"  {' '.join(text.split())[:80] or '(no text)'} -> {href}")
            if a.screenshot:
                page.screenshot(path=a.screenshot, full_page=True)
                print(f"\nScreenshot: {a.screenshot}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
