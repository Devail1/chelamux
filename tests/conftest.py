"""Suite-wide isolation from the developer's real chela install.

``chela/config.py`` sources ``$CHELA_DIR/chela.env`` at import — that is the point of the
env file: one place, read by every process. A test *is* one of those processes, so on a
machine that actually runs chela the suite would silently inherit that machine's session
name, dashboard port and terminal flags, and start passing (or failing) for reasons that
have nothing to do with the code. This repo has already shipped three bugs that were green
in CI and broken live; a suite that reads live state is how that keeps happening.

``CHELA_ENV_FILE=""`` turns the file off. It is set here, at conftest import — before any
test module imports ``chela.config`` — because the load happens at import time and no
fixture runs early enough to prevent it.
"""
import os

os.environ.setdefault("CHELA_ENV_FILE", "")
