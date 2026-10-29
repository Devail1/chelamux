### Changed

- **The install instructions now point at PyPI.** The landing hero is `uvx chelamux dashboard`,
  the judge-sweep one-liner uses `--from chelamux`, and the docs quickstart leads with
  `uv tool install "chelamux[dashboard,telegram]"`, keeping `git clone` + `uv sync --all-extras`
  as the from-source path. PyPI gets tagged releases only; a `git+https://` install tracks the
  default branch. The README and Getting Started install block stays on `git+https://`,
  because the fresh-install check swaps that URL for the commit under review. (CMX-367)
