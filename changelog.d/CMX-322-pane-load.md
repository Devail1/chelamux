### Fixed

- **A wall pane opens much faster, most of all on a phone.** Every pane used to download
  ttyd's ~720 kB client uncompressed, revalidate every font, and wait for a 2.3 MB icon
  font before applying the terminal font. ttyd's client is now served once, gzipped and
  cached for good; the terminal fonts are woff2 at content-hashed, `immutable` URLs; the
  icon font is split into small blocks a pane only downloads when it shows one of their
  glyphs; and the font apply no longer waits for icons. On a throttled phone profile, a
  pane with a warm cache shows its terminal in about 0.75 s (was 1.35 s), and a cold pane
  has its fonts applied in 1.5 s (was 5 s). Material Design Icons (U+F0001-F1AF0) are no
  longer bundled for the web terminal. (CMX-322)
