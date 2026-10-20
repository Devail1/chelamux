### Changed

- **The Wall's tile model, wire geometry and window-id parsing are TypeScript (CMX-212 P22).**
  `wallmodel`, `wire` and `windowid` are converted in place under `static/js/`, typechecked
  strictly with the frontend. A room badge on a Wall tile now shows a plug icon instead of the
  🔌 emoji. (CMX-236)
