"""chela — a tiny control plane for a fleet of Claude Code agents on tmux."""

from importlib.metadata import PackageNotFoundError, version

try:
    # pyproject.toml's `version` field is the ONLY place this number is written.
    # Deriving it here (instead of a second hardcoded literal) is what makes it
    # a single fact — the two could otherwise drift silently, as they briefly
    # did across 0.2.0 and 0.3.0.
    __version__ = version("chelamux")
except PackageNotFoundError:  # pragma: no cover - only when chelamux isn't installed at all
    __version__ = "0.0.0+unknown"
