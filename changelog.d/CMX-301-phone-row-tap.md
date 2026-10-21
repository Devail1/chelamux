### Fixed

- **Tapping a session in the phone sidebar opens it again.** Since the sidebar moved to React,
  a tap on a session row on a phone showed the row's tooltip ("window: …") and the session
  never opened. The dashboard's touch tooltip now leaves everything React draws alone, so the
  tap opens that session full-screen and closes the drawer. (CMX-301)
