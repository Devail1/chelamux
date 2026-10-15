"""CMX-149: deploy-landing.yml's "production serves this commit" check vs the CF beacon.

Cloudflare Web Analytics injects its beacon ``<script>`` into every page chela.pages.dev
serves, so the old token diff against ``landing/index.html`` failed on every deploy. The
comparison now lives in ``scripts/landing_verify.py``, which strips ONLY that element. The
negative controls here are the point: a one-character body change must still fail, or the
check is back to proving nothing about which build production serves.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "landing_verify.py"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-landing.yml"

# Byte-for-byte what Cloudflare appended before </body> on 2026-10-15 (token is public:
# it ships in every served page).
BEACON = (
    "<!-- Cloudflare Pages Analytics --><script defer "
    "src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"4e12d1e957284239b768b851de5aaf45\"}'></script>"
    "<!-- Cloudflare Pages Analytics -->"
)

SOURCE = """<!doctype html>
<html>
<head><title>chela</title></head>
<body>
  <h1>Mission control for your agents.</h1>
  <p class="sub">Dispatch, judge, merge.</p>
</body>
</html>
"""


def _mod():
    spec = importlib.util.spec_from_file_location("chela_landing_verify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _served(source: str) -> str:
    return source.replace("</body>", BEACON + "</body>")


def _run(tmp_path, source: str, served: str):
    src, live = tmp_path / "index.html", tmp_path / "live.html"
    src.write_text(source)
    live.write_text(served)
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(src), str(live)],
        capture_output=True, text=True, timeout=30,
    )


# --- the three cases the brief names, through the real CLI --------------------------------

def test_source_plus_beacon_matches(tmp_path):
    r = _run(tmp_path, SOURCE, _served(SOURCE))
    assert r.returncode == 0, r.stderr


def test_changed_source_plus_beacon_does_not_match(tmp_path):
    changed = SOURCE.replace("Mission control", "Mission controls")
    r = _run(tmp_path, SOURCE, _served(changed))
    assert r.returncode == 1
    assert "first difference" in r.stderr
    assert "controls" in r.stderr


def test_source_without_beacon_matches(tmp_path):
    r = _run(tmp_path, SOURCE, SOURCE)
    assert r.returncode == 0, r.stderr


def test_pages_whitespace_rewrite_still_matches(tmp_path):
    reflowed = _served(SOURCE).replace("\n  <h1>", "  \n\n<h1>").replace("<p class", "<p\nclass")
    assert _run(tmp_path, SOURCE, reflowed).returncode == 0


def test_whitespace_inside_a_token_still_counts(tmp_path):
    # Only runs of spaces/newlines collapse; removing one entirely joins two tokens.
    assert _run(tmp_path, SOURCE, _served(SOURCE.replace("Mission control", "Missioncontrol"))).returncode == 1


# --- fail closed: unreadable/empty never reads as a match --------------------------------

@pytest.mark.parametrize("which", ["source", "served"])
def test_empty_file_is_exit_2(tmp_path, which):
    r = _run(tmp_path, "" if which == "source" else SOURCE, "" if which == "served" else SOURCE)
    assert r.returncode == 2
    assert "is empty" in r.stderr


def test_both_empty_is_not_a_match(tmp_path):
    assert _run(tmp_path, "  \n", "\n").returncode == 2


def test_missing_file_is_exit_2(tmp_path):
    src = tmp_path / "index.html"
    src.write_text(SOURCE)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), str(src), str(tmp_path / "nope.html")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 2
    assert "cannot read" in r.stderr


def test_undecodable_file_is_exit_2(tmp_path):
    src, live = tmp_path / "index.html", tmp_path / "live.html"
    src.write_text(SOURCE)
    live.write_bytes(b"\xff\xfe\xfa not utf-8")  # UnicodeDecodeError is not an OSError
    r = subprocess.run([sys.executable, str(SCRIPT), str(src), str(live)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "cannot read" in r.stderr


@pytest.mark.parametrize("argv", [[], ["one"], ["a", "b", "c"]])
def test_wrong_arity_is_exit_2(argv):
    r = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "usage:" in r.stderr


# --- the strip removes the beacon and nothing else ---------------------------------------

def test_strip_removes_the_whole_injected_element():
    assert _mod().strip_beacon("a" + BEACON + "b") == "ab"


@pytest.mark.parametrize("beacon", [
    # double quotes, no marker comments
    '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
    'data-cf-beacon=\'{"token": "x"}\'></script>',
    # data-cf-beacon before src, whitespace between tags
    "<!-- Cloudflare Pages Analytics -->\n<script data-cf-beacon='{}' defer "
    "src='https://static.cloudflareinsights.com/beacon.min.js'> </script>\n"
    "<!-- Cloudflare Pages Analytics -->",
])
def test_strip_tolerates_beacon_variants(beacon):
    assert _mod().strip_beacon("a" + beacon + "b") == "ab"


@pytest.mark.parametrize("other", [
    # a different script — even another cloudflareinsights path — is NOT the beacon
    "<script defer src='https://static.cloudflareinsights.com/other.min.js'></script>",
    "<script src='https://evil.example/beacon.min.js'></script>",
    "<script src='https://static.cloudflareinsights.com/beacon.min.js.evil'></script>",
    # an inline script carrying the URL as text, not as its src
    "<script>load('https://static.cloudflareinsights.com/beacon.min.js')</script>",
    # the marker comment alone, without the beacon
    "<!-- Cloudflare Pages Analytics -->",
])
def test_strip_leaves_everything_else(other):
    assert _mod().strip_beacon("a" + other + "b") == "a" + other + "b"


def test_injected_non_beacon_script_does_not_match(tmp_path):
    served = SOURCE.replace("</body>", "<script src='https://evil.example/x.js'></script></body>")
    assert _run(tmp_path, SOURCE, served).returncode == 1


# --- the workflow actually runs it --------------------------------------------------------

def _verify_step():
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = [s for s in wf["jobs"]["deploy"]["steps"]
             if s.get("name") == "Verify the production URL serves this commit"]
    assert len(steps) == 1
    return steps[0]


def test_verify_step_gates_on_the_script():
    step = _verify_step()
    assert "if" not in step
    run = step["run"]
    assert "if python3 scripts/landing_verify.py landing/index.html /tmp/live.html; then" in run
    assert run.index("curl -fsSL") < run.index("scripts/landing_verify.py")
    # the old beacon-blind byte/token diff is gone
    assert "diff -q" not in run
    assert run.rstrip().endswith("exit 1")


def test_script_changes_trigger_a_redeploy():
    wf = yaml.safe_load(WORKFLOW.read_text())
    assert "scripts/landing_verify.py" in wf[True]["push"]["paths"]
