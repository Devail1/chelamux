## 99. An inline page script is source-matched because it has no module to import

**Assertion form:** a static page (`landing/index.html`) carries its behaviour in an inline
`<script>` — no `.js` file, no export, nothing a test can `import`. So the guard falls back
to reading the HTML as text: `assert "btn.hidden = false" in html`, next to a regex that the
button ships with the `hidden` attribute. It reads as "the button is hidden until the script
reveals it". Each check is true of the file, so it reads as covered.

**Mutation that defeats it:** leave the reveal statement alone and dead-code the early exit
*above* it — `if (!src || !navigator.clipboard) continue;` →
`if (true || !src || !navigator.clipboard) continue;`. Every iteration now `continue`s,
the button never un-hides, and the copy feature is gone. The guarded substring is still
there, byte for byte and not even wrapped. This is [[01|shape 1]] one statement removed:
there the dead code wraps the guarded statement, here it sits on the path *to* it. A
source-text guard can only see the line it names, never whether control reaches it.

The same page had the CSS sibling of this: `\.copy\[hidden\]\{display:none\}` matched
anywhere in the HTML, so `/* .copy[hidden]{display:none} */` (or the rule moved into an
`@media` block that never matches) stayed green, as did a Releases link inside `<!-- -->`.

**Why it looks covered:** "there is no module, so text is the only handle" sounds like a
real limit. It isn't. jsdom can build a window from the page and `window.eval` the inline
script, exactly as a browser with JS on would.

**Guard form that survives:** lift the inline `<script>` bodies out of the real page file,
build a jsdom window from that same file, stub only what jsdom lacks (`navigator.clipboard`,
`innerText`, `setTimeout` captured), run the script, and assert on the DOM: `btn.hidden`
is `false` with a clipboard and `true` without one or without a target, a click writes the
exact command, and the failure path selects the text. For facts jsdom cannot compute (its
`getComputedStyle` reports `[hidden]` as `display:none` even with the author rule deleted,
so it can't tell whether the cascade re-hides the button), strip HTML and CSS comments and
drop `@`-blocks before matching, so a commented-out or never-applying rule cannot pass.

**Found:** CMX-99 judge round 1, PR #672. Fixed in `tests/landing_copy_button.test.mjs`
(runs the page's own script) and `tests/test_landing_page.py` (`_html()` strips comments,
`_top_level_css()` drops comments and `@`-blocks).
