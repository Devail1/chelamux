### Fixed

- **An override can be approved from a phone.** `chela merge --override` now reaches
  Telegram as a card with **Approve / Deny** buttons in the orchestrator's topic (General if
  none is bound); only a user listed in `TELEGRAM_OPERATOR_ID` can press them, anyone else's
  tap changes nothing, and the card is edited to show approved / denied / expired. The push
  notification carries an absolute dashboard link built from the new
  `CHELA_DASHBOARD_PUBLIC_URL` (or says there is none, instead of a dead relative path). The
  approval window is a Dispatch-tab setting with a 15 min default (was a fixed 300s), and a
  pending request is listed in the Decisions inbox until it is decided or expires. (CMX-61)
