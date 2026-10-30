"""chela — a tiny control plane for a fleet of Claude Code agents on tmux."""

from importlib.metadata import PackageNotFoundError, version


def _package_version() -> str:
    """The installed chelamux version, or ``0.0.0+unknown`` from a bare source tree."""
    try:
        # pyproject.toml's `version` field is the ONLY place this number is written.
        # Deriving it here (instead of a second hardcoded literal) is what makes it
        # a single fact — the two could otherwise drift silently, as they briefly
        # did across 0.2.0 and 0.3.0.
        return version("chelamux")
    except PackageNotFoundError:  # a source tree with no installed metadata
        return "0.0.0+unknown"


__version__ = _package_version()
