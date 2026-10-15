### Fixed

- **The dashboard's first paint is the view it lands on.** With terminals on, the Wall is
  the server-rendered active view, so a load no longer flashes the Work view first; a phone
  no longer flashes the desktop "+ New shell" button; and a collapsed desktop sidebar stays
  collapsed from the first frame. (CMX-89, #658)
