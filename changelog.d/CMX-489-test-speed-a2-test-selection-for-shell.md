### Changed

- **A shell-script mutation runs only the tests that can observe it.** A `.sh` file used to
  run the full suite for every judge and self-check experiment. Its observers are now:
  - the test files that name it;
  - the tests that observe a Python module naming it (`chela/main.py` names
    `scripts/agent-terminals.sh`);
  - the same for another shell script that names it (`run-chela.sh` sources `chela-env.sh`).

  A subset survivor is still confirmed on the full suite, and a file under
  `.github/workflows/` still runs the full suite.

  (CMX-489)
