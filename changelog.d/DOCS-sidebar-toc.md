### Added

- **A sticky sidebar table of contents on the docs page**, with scroll-spy marking the
  section you are actually reading. At 13 sections the old inline card had stopped being
  navigation and become a list you scrolled past once. The same `<nav class="toc">` element
  is the card on narrow screens and the sidebar on wide ones — one element, two behaviours,
  so there is no second copy of the section list to drift.

### Fixed

- **The page heading sat flush against the left edge of a phone screen.** `.doc-hero`'s
  `padding:36px 0 8px` shorthand zeroed the side padding it was inheriting from `.wrap`;
  `padding-block` leaves the gutter alone. The landing page's `.hero` had the identical bug
  and is fixed with it.
