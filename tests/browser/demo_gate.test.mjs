// CMX-93 — scripts/demo/gate.mjs, the merge-gate demo's renderer, run for real.
//
// Its clock is the script's, not the wall's: every picture is a screenshot with a fixed
// duration in frames.txt. So one scene of each kind has an exact, predictable duration
// list — typing (two characters a frame), the comment hold (no scroll: it fits), the
// Telegram card, its three-step tap and its edited state — and a renderer that drops or
// reorders a beat changes it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchChromium } from './fixture.mjs';
import { markdown, ttyLine } from '../../scripts/demo/gate.mjs';

const GATE = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts', 'demo', 'gate.mjs');
const run = (...args) => spawnSync(process.execPath, [GATE, ...args], { encoding: 'utf8', timeout: 120000 });

test('gate.mjs wants a scenes file and an out dir', () => {
    for (const args of [[], ['scenes.json']]) {
        const r = run(...args);
        assert.equal(r.status, 2);
        assert.equal(r.stderr, 'usage: gate.mjs <scenes.json> <out-dir>\n');
    }
});

test('each scene kind renders its beats with the script\'s own durations', async (t) => {
    const { browser, why } = await launchChromium();
    if (!browser) { t.skip(why); return; }
    await browser.close();
    const dir = mkdtempSync(join(tmpdir(), 'chela-demo-gate-'));
    writeFileSync(join(dir, 'scenes.json'), JSON.stringify([
        { kind: 'term', caption: 'c1', title: 'agent', lines: ['$ abcd', 'merge REFUSED: no'] },
        { kind: 'comment', caption: 'c2', body: '## ⚖️ head\n\n```diff\n- a\n+ b\n```\n* x' },
        { kind: 'telegram', caption: 'c3', text: 'card', after: 'card\n\n✅ merged' },
    ]));
    const r = run(join(dir, 'scenes.json'), dir);
    assert.equal(r.status, 0, r.stderr);
    const list = readFileSync(join(dir, 'gate.frames', 'frames.txt'), 'utf8').trim().split('\n');
    const durations = list.filter((l) => l.startsWith('duration ')).map((l) => Number(l.slice(9)));
    assert.deepEqual(durations, [0.045, 0.045, 0.045, 0.4, 3.1, 3, 6, 4, 0.08, 0.08, 0.08, 5]);
    const files = list.filter((l) => l.startsWith('file '));
    assert.equal(files.at(-1), files.at(-2), 'the last picture is listed twice so its duration counts');
    assert.equal(readdirSync(join(dir, 'gate.frames')).filter((f) => f.endsWith('.png')).length, 12);
});

test('a comment taller than the window scrolls down to its diff, then holds there', async (t) => {
    const { browser, why } = await launchChromium();
    if (!browser) { t.skip(why); return; }
    await browser.close();
    const dir = mkdtempSync(join(tmpdir(), 'chela-demo-gate-'));
    const filler = Array.from({ length: 60 }, (_, i) => `* filler line ${i}`).join('\n');
    writeFileSync(join(dir, 'scenes.json'), JSON.stringify([
        { kind: 'comment', caption: 'c', body: `## head\n\n${filler}\n\n\`\`\`diff\n- a\n+ b\n\`\`\`` },
    ]));
    const r = run(join(dir, 'scenes.json'), dir);
    assert.equal(r.status, 0, r.stderr);
    const list = readFileSync(join(dir, 'gate.frames', 'frames.txt'), 'utf8').trim().split('\n');
    const durations = list.filter((l) => l.startsWith('duration ')).map((l) => Number(l.slice(9)));
    const files = list.filter((l) => l.startsWith('file ')).map((l) => l.slice(6, -1));
    // The top (3 s), at least one 24 px scroll step (0.04 s each), the diff held (6 s).
    assert.equal(durations[0], 3);
    assert.equal(durations.at(-1), 6);
    const steps = durations.slice(1, -1);
    assert.ok(steps.length > 1 && steps.every((d) => d === 0.04), String(durations));
    const png = (f) => readFileSync(join(dir, 'gate.frames', f));
    const held = files.at(-1);             // listed twice (concat), so its step is at(-3)
    assert.ok(!png(files[0]).equals(png(held)), 'the held frame is scrolled, not the top again');
    assert.ok(!png(files.at(-3)).equals(png(held)), 'the hold lands on the target, past the last step');
});

test('ttyLine colours a prompt, a refusal, a success and a diff line, and dims the rest', () => {
    assert.equal(ttyLine('$ chela merge <x>'), '<span class="p">$</span> chela merge &lt;x&gt;');
    assert.equal(ttyLine('merge REFUSED: no'), '<span class="bad">merge REFUSED: no</span>');
    assert.equal(ttyLine('⛔ NEVER: main'), '<span class="bad">⛔ NEVER: main</span>');
    for (const ok of ['✅ Merged', '→ PR opened', '⏳ pending', '+ a test']) {
        assert.equal(ttyLine(ok), `<span class="ok">${ok}</span>`);
    }
    assert.equal(ttyLine('  Options: $ x'), '<span class="dim">  Options: $ x</span>');
});

test('markdown renders the judge comment\'s headings, diff fence, bullets, table and footer', () => {
    assert.equal(markdown('## A **b**\n### C `d`'), '<h2>A <b>b</b></h2><h3>C <code>d</code></h3>');
    assert.equal(markdown('```diff\n- if x:\n+ if y:\n```'),
        '<pre class="hl"><span class="del">- if x:</span>\n<span class="add">+ if y:</span></pre>');
    assert.equal(markdown('* one\n\ntwo'), '<li>one</li><p>two</p>');
    assert.equal(markdown('_PR: x_'), '<p><i>PR: x</i></p>');
    assert.equal(markdown('| a | b \\| c |\n|---|---|\n| 1 | 2 |'),
        '<table><tr><td>a</td><td>b | c</td></tr><tr><td>1</td><td>2</td></tr></table>');
});
