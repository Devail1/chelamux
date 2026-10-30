### Added

- **`chela --version`** (and `chelamux --version`) prints `chela <version>` from the installed
  package's metadata, or `chela 0.0.0+unknown` from a source tree with none. It used to fail
  with `unrecognized arguments`. A test now also reads the landing hero's install command out
  of the page and fails if README.md stops showing it verbatim. (CMX-368)
