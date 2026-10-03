### Changed

- **The Linear tracker keeps the 100 most recently finished issues visible and archives
  only older ones.** Before, chela archived an issue as soon as it was Done or Canceled,
  which emptied the board of recent history. A new `tracker:` option, `keep_done`
  (default 100), sets how many finished issues stay unarchived, ranked by when they
  finished. `keep_done: 0` archives every finished issue, as before. Open issues are never
  archived and never count toward it. Closing a merged task now only marks it Done; the
  sweep does the archiving, every 15 minutes and once right after a close. Once the sweep
  has caught up, the free plan's headroom is 250 − open − `keep_done`. `chela doctor`
  still WARNs above 200 non-archived issues, and now names lowering `keep_done` as the
  fix. (CMX-8)
