"""CMX-354: an agent's own pytest caps each test process's memory (``tests/memory_cap.py``).

The end-to-end tests run a small inner pytest session in a subprocess with the REAL
``tests/conftest.py`` loaded as a plugin (``-p conftest``), so the wiring is what is under
test, not a copy of it. The inner session never reads this box's real cgroup: the outer
suite may itself be running inside a ``chela-suite-*`` scope, which would switch the cap
off. A one-line ``-p fakecg`` plugin points ``memory_cap.CGROUP_FILE`` at a fixture file
before the session starts.

Allocations use ``bytes(n)``: glibc maps a large ``calloc`` fresh and never touches it, so
reaching a 512 MiB cap costs address space, not RAM.
"""
import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import memory_cap

TESTS_DIR = Path(__file__).resolve().parent
GIB = 1024 ** 3
MIB = 1024 ** 2

NOT_CAPPED = ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-tmux.slice/"
              "tmux-spawn-f03bbe5c-d104-4c80-a648-87d3f8d9fa41.scope\n")
SUITE_SCOPE = ("0::/user.slice/user-1000.slice/user@1000.service/chela-agents.slice/"
               "chela-suite-3fa9c0d1e2b4.scope\n")
MEMCAP_SLICE = "0::/user.slice/user-1000.slice/user@1000.service/memcap.slice/run-r1.scope\n"

INNER_TESTS = textwrap.dedent('''
    import os, resource, subprocess

    CHUNK = 64 * 1024 * 1024
    SAFETY = 64 * 1024 ** 3          # a broken cap must not grow forever

    def _grow(limit):
        hold, total = [], 0
        while total < limit:
            hold.append(bytes(CHUNK))
            total += CHUNK
        return total

    def test_a_runaway():
        _grow(int(os.environ.get("GROW_TO", SAFETY)))

    def test_b_after():
        print("LIMIT", resource.getrlimit(resource.RLIMIT_AS)[0])
        print("PARENT", open(f"/proc/{os.getppid()}/limits").read())
        out = subprocess.run(["sh", "-c", "ulimit -v"], capture_output=True, text=True)
        print("CHILD", out.stdout.strip())
        print("AFTER", resource.getrlimit(resource.RLIMIT_AS)[0])
        assert 0, "report"
''')


def _inner(tmp_path, cgroup: str, *pytest_args, **env) -> subprocess.CompletedProcess:
    (tmp_path / "cgroup").write_text(cgroup)
    (tmp_path / "fakecg.py").write_text(
        "import os, pathlib, memory_cap\n"
        "memory_cap.CGROUP_FILE = pathlib.Path(os.environ['FAKE_CGROUP'])\n")
    (tmp_path / "test_inner.py").write_text(INNER_TESTS)
    run_env = {k: v for k, v in os.environ.items()
               if k not in (memory_cap.ENV_VAR, "PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER")}
    run_env.update(FAKE_CGROUP=str(tmp_path / "cgroup"),
                   PYTHONPATH=os.pathsep.join([str(tmp_path), str(TESTS_DIR)]), **env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "fakecg", "-p", "conftest", "-o", "addopts=",
         "-p", "no:cacheprovider", "-rA", "--rootdir", str(tmp_path), *pytest_args,
         str(tmp_path / "test_inner.py")],
        cwd=tmp_path, env=run_env, capture_output=True, text=True, timeout=120)


def _report(out: str, key: str) -> str:
    for line in out.splitlines():
        if line.startswith(key + " "):
            return line.split(" ", 1)[1]
    raise AssertionError(f"no {key} line in:\n{out}")


def _parent_as_limit(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("Max address space"):
            return line.split()[3]
    raise AssertionError(f"no parent limits in:\n{out}")


def test_runaway_test_dies_of_memoryerror_in_its_worker_and_the_session_continues(tmp_path):
    r = _inner(tmp_path, NOT_CAPPED, "-n", "1", CHELA_TEST_MEMORY_CAP="512M")
    out = r.stdout + r.stderr
    assert "FAILED test_inner.py::test_a_runaway - MemoryError" in out, out
    assert "2 failed" in out and "crashed" not in out, out    # test_b ran in the same worker
    assert _report(out, "LIMIT") == str(512 * MIB)
    # the worker's children are exempt, and the worker is capped again after the spawn
    assert _report(out, "CHILD") == "unlimited"
    assert _report(out, "AFTER") == str(512 * MIB)
    # the xdist controller runs no tests and is not capped
    assert _parent_as_limit(out) == "unlimited"
    assert ("chela tests: memory cap ON, 0.5 GiB of address space per test process "
            "(subprocesses exempt); raise it with CHELA_TEST_MEMORY_CAP=6G, or "
            "CHELA_TEST_MEMORY_CAP=0 to turn it off") in out


def test_a_run_without_xdist_caps_its_one_process(tmp_path):
    r = _inner(tmp_path, NOT_CAPPED, "-p", "no:xdist", CHELA_TEST_MEMORY_CAP="512M")
    out = r.stdout + r.stderr
    assert "FAILED test_inner.py::test_a_runaway - MemoryError" in out, out
    assert _report(out, "LIMIT") == str(512 * MIB)
    assert "chela tests: memory cap ON, 0.5 GiB" in out


def test_negative_control_without_the_cap_the_allocation_grows(tmp_path):
    # the same bounded growth, to twice the cap that stopped it above, now succeeds
    r = _inner(tmp_path, NOT_CAPPED, "-n", "1", CHELA_TEST_MEMORY_CAP="0",
               GROW_TO=str(1024 * MIB))
    out = r.stdout + r.stderr
    assert "PASSED test_inner.py::test_a_runaway" in out, out
    assert _report(out, "LIMIT") == str(resource.RLIM_INFINITY)
    assert "memory cap ON" not in out


def test_the_same_growth_under_the_cap_fails(tmp_path):
    r = _inner(tmp_path, NOT_CAPPED, "-n", "1", CHELA_TEST_MEMORY_CAP="512M",
               GROW_TO=str(1024 * MIB))
    assert "FAILED test_inner.py::test_a_runaway - MemoryError" in r.stdout, r.stdout


@pytest.mark.parametrize("cgroup", [SUITE_SCOPE, MEMCAP_SLICE], ids=["chela-suite", "memcap"])
def test_inside_a_capped_cgroup_no_limit_is_set(tmp_path, cgroup):
    r = _inner(tmp_path, cgroup, "-n", "1", CHELA_TEST_MEMORY_CAP="512M",
               GROW_TO=str(1024 * MIB))
    out = r.stdout + r.stderr
    assert "PASSED test_inner.py::test_a_runaway" in out, out
    assert _report(out, "LIMIT") == str(resource.RLIM_INFINITY)
    assert "memory cap ON" not in out


@pytest.mark.parametrize("text, capped", [
    (SUITE_SCOPE, True),
    ("0::/user.slice/chela-suite-abc.scope", True),
    ("4:memory:/user.slice/chela-suite-abc.scope\n1:name=systemd:/x", True),
    (MEMCAP_SLICE, True),
    ("0::/user.slice/run-memcap-7.scope", True),
    (NOT_CAPPED, False),
    ("0::/user.slice/user-1000.slice/user@1000.service/chela-agents.slice/x.scope", False),
    ("0::/user.slice/chela-suite.scope", False),
    ("0::/user.slice/xchela-suite-abc.scope", False),
    ("", False),
])
def test_in_capped_scope(text, capped):
    assert memory_cap.in_capped_scope(text) is capped


@pytest.mark.parametrize("raw, expected", [
    (None, 3221225472),
    ("", 3221225472),
    ("  ", 3221225472),
    ("6G", 6442450944),
    ("6gib", 6442450944),
    ("512M", 536870912),
    ("1.5G", 1610612736),
    ("1048576", 1048576),
    ("0", 0),
    ("lots", 3221225472),          # a typo keeps the cap on
    ("-1G", 3221225472),
    ("nanG", 3221225472),
    ("inf", 3221225472),
    ("1e999G", 3221225472),
])
def test_cap_size_precedence(raw, expected):
    env = {} if raw is None else {"CHELA_TEST_MEMORY_CAP": raw}
    assert memory_cap.cap_bytes(env) == expected


def test_wanted_cap_reads_the_cgroup_file(tmp_path, monkeypatch):
    cg = tmp_path / "cgroup"
    monkeypatch.setattr(memory_cap, "CGROUP_FILE", cg)
    env = {"CHELA_TEST_MEMORY_CAP": "1G"}
    assert memory_cap.wanted_cap(env) == GIB             # unreadable: the cap stays on
    cg.write_text(NOT_CAPPED)
    assert memory_cap.wanted_cap(env) == GIB
    cg.write_text(SUITE_SCOPE)
    assert memory_cap.wanted_cap(env) == 0


def _py(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(TESTS_DIR)}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("before, expected", [
    ("(1 << 30, 8 << 30)", f"True ({1 << 30}, {8 << 30})"),       # a lower soft limit stays
    ("(2 << 30, 2 << 30)", f"True ({2 << 30}, {2 << 30})"),       # never above the hard one
    ("(resource.RLIM_INFINITY, resource.RLIM_INFINITY)", f"True ({3 << 30}, -1)"),
])
def test_install_never_raises_a_lower_limit_and_keeps_the_hard_limit(before, expected):
    r = _py(f"""
        import resource, memory_cap
        resource.setrlimit(resource.RLIMIT_AS, {before})
        print(memory_cap.install(3 << 30), resource.getrlimit(resource.RLIMIT_AS))
    """)
    assert r.stdout.strip() == expected, r.stderr


def test_install_zero_is_off():
    r = _py("""
        import resource, subprocess, memory_cap
        print(memory_cap.install(0), resource.getrlimit(resource.RLIMIT_AS)[0],
              subprocess.Popen._execute_child is memory_cap._execute_child_uncapped)
    """)
    assert r.stdout.strip() == f"False {resource.RLIM_INFINITY} False", r.stderr


def test_a_child_gets_back_the_limit_from_before_the_cap():
    r = _py("""
        import resource, subprocess, memory_cap
        resource.setrlimit(resource.RLIMIT_AS, (4 << 30, resource.RLIM_INFINITY))
        memory_cap.install(1 << 30)
        out = subprocess.run(["sh", "-c", "ulimit -v"], capture_output=True, text=True)
        print(out.stdout.strip(), resource.getrlimit(resource.RLIMIT_AS)[0])
    """)
    assert r.stdout.strip() == f"{(4 << 30) // 1024} {1 << 30}", r.stderr


def test_a_second_install_keeps_the_childrens_limit_from_before_the_first():
    r = _py("""
        import resource, subprocess, memory_cap
        memory_cap.install(2 << 30)
        memory_cap.install(1 << 30)
        out = subprocess.run(["sh", "-c", "ulimit -v"], capture_output=True, text=True)
        print(out.stdout.strip(), resource.getrlimit(resource.RLIMIT_AS)[0])
    """)
    assert r.stdout.strip() == f"unlimited {1 << 30}", r.stderr


def test_a_failed_spawn_still_restores_the_cap():
    r = _py("""
        import resource, subprocess, memory_cap
        memory_cap.install(1 << 30)
        try:
            subprocess.run(["/nonexistent/binary"])
        except FileNotFoundError:
            print("raised", resource.getrlimit(resource.RLIMIT_AS)[0])
    """)
    assert r.stdout.strip() == f"raised {1 << 30}", r.stderr


@pytest.mark.parametrize("exc", ["ValueError('bad')", "PermissionError('no')"])
def test_a_refused_limit_is_reported_not_raised(exc):
    r = _py(f"""
        import resource, subprocess, memory_cap
        def refuse(*_a):
            raise {exc}
        resource.setrlimit = refuse
        print(memory_cap.install(1 << 30),
              subprocess.Popen._execute_child is memory_cap._execute_child_uncapped)
    """)
    assert r.stdout.strip() == "False False", r.stderr
