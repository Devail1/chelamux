## 93b. A renderer is tested by its frame clock, so no test reads the words on a frame

**Assertion form:** a script renders scenes to screenshots, each with a fixed duration in an
ffmpeg concat list. The test feeds it one scene of each kind and pins the exact duration
list, the picture count, and that some pictures differ from others (`!png(a).equals(png(b))`).
A renderer that drops, adds or reorders a beat changes the list, so it looks fully pinned.

**Mutation that defeats it:** anything that changes what a picture *says* without changing
when it is taken. Stop setting the caption, render the card's pre-tap text again after the
tap, drop the "edited" footer, leave the tap circle on the next scene, or stop escaping one
field. Every one of them keeps the same shots at the same durations, and the pictures still
differ from each other. The suite stays green.

**Why it looks covered:** the beats ARE the renderer's control flow, and every branch of it
ends in a timed `shot()`. So a duration list feels like a trace of everything it does. It
is a trace of *when*, never of *what*. Comparing pictures to each other proves something
changed between two frames, not that the right thing is on either one. The scene data's
own words were pinned in the producer's test, which made the renderer feel covered by
proxy.

**Guard form that survives:** have the renderer record what each picture shows next to the
picture itself: its visible text (`document.body.innerText`) and any overlay state, written
in the same order as the concat list. Then pin the exact per-frame text sequence for a fixture
whose scene text contains real markup. One trap: the markup has to be a token the HTML parser
treats as a tag. `<1>` is parsed as text, so an unescaped `card <1>` still reads `card <1>` and
a dropped `esc()` survives. `<u>1` disappears when unescaped. That is
[[29|an exact-output fixture whose payload is identity under the mutation]], one layer down,
in the browser's parser.

**Found:** CMX-93 rework round 2 (2026-10-17), PR #736. The judge's held-out experiments and
its note "renderer test pins timing, not text" on `tests/browser/demo_gate.test.mjs`.
Closed there: `scripts/demo/gate.mjs` writes `gate.frames/text.json` (each picture's text and
tap count), and the test pins it frame by frame, using markup that only survives escaping.
