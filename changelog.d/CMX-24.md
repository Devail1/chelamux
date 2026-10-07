### Fixed

- **Telegram: a screenshot an agent opens with `Read` now relays as a photo.** With tool calls
  hidden (the default) the image-bearing tool result was dropped whole, and the Read tool's
  "[Image: original … Multiply coordinates …]" note relayed as a 👤 user message instead. The
  photo now posts (tool text stays hidden) and the note is never relayed. (CMX-24, #608)
