### Changed

- **The dashboard's data layer is TypeScript (CMX-212 P03).** The refresh loop, the one
  Server-Sent Events stream, the HTTP client and the page state moved from `main.js`,
  `sse.js` and `util.js` to `frontend/src/shared/` (`scheduler`, `events`, `api`, `store`).
  Each feature registers its own polls and event handlers, the page reads its config from one
  JSON block instead of three inline globals, and legacy imports are extensionless. With
  `CHELA_TERMINALS_ENABLED=false` the page now really runs without terminals: it used to read
  the missing global as "on". (CMX-217)
