// Render the merge-gate demo's scenes (gate.py) into frames for record-gate.sh.
//
//   node scripts/demo/gate.mjs <scenes.json> <out-dir>
//
// Writes <out-dir>/gate.frames/ — one PNG per distinct picture plus an ffmpeg concat list
// with each picture's duration, and text.json: what each picture says (its visible text,
// and whether the tap is on it), so the words on camera can be checked without OCR. Time is the script's, not the wall clock's: every frame is
// a screenshot taken once the page has settled, so the clip is identical from run to run.
// The page is self-contained (no network); every line of text on it came out of gate.py.
import { chromium } from 'playwright';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Symbola (shipped with the dashboard) covers ⚖ ⏱ ⛔ ✅ ❌ ⏳ on a host with no emoji font.
const SYMBOLS = readFileSync(join(dirname(fileURLToPath(import.meta.url)),
    '../../chela/dashboard/static/fonts/Symbola-Subset.ttf')).toString('base64');
const CSS = `
  @font-face{font-family:sym;src:url(data:font/ttf;base64,${SYMBOLS})}
  *{box-sizing:border-box} body{margin:0;width:1440px;height:900px;background:#14161a;color:#e6e6e6;
    font:18px/1.45 system-ui,sans-serif,sym;display:flex;flex-direction:column;align-items:center}
  #cap{margin:44px 0 30px;font:600 30px/1.2 system-ui,sans-serif,sym;color:#fff;text-align:center;max-width:1240px}
  #stage{width:1240px;flex:1;min-height:0;margin-bottom:44px;display:flex;justify-content:center;align-items:flex-start}
  .win{width:100%;border-radius:12px;overflow:hidden;background:#0d0f12;box-shadow:0 18px 60px #0009;
    display:flex;flex-direction:column;max-height:100%}
  .bar{padding:10px 16px;background:#23262c;color:#9aa0a6;font-size:15px}
  .bar b{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:7px;vertical-align:-1px}
  pre.tty{margin:0;padding:22px 26px;font:19px/1.5 ui-monospace,'DejaVu Sans Mono',monospace,sym;
    white-space:pre-wrap;color:#d7dae0;overflow:hidden}
  .p{color:#7ee787}.ok{color:#7ee787}.bad{color:#ff7b72}.dim{color:#8b949e}
  .gh{background:#0d1117;border:1px solid #30363d;overflow:hidden}
  .gh .bar{background:#161b22;color:#c9d1d9;font-weight:600;border-bottom:1px solid #30363d}
  .md{position:relative;padding:4px 30px 24px;overflow:hidden;font-size:17px;color:#c9d1d9}
  .md h2{font-size:25px;border-bottom:1px solid #30363d;padding-bottom:8px}.md h3{font-size:20px}
  .md code{background:#262c36;padding:1px 6px;border-radius:5px;font-family:ui-monospace,monospace}
  .md pre{background:#161b22;padding:12px 16px;border-radius:8px;font:19px/1.5 ui-monospace,monospace,sym}
  .md table{border-collapse:collapse}.md td,.md th{border:1px solid #30363d;padding:6px 10px}
  .md .add{color:#7ee787;background:#12261e}.md .del{color:#ff7b72;background:#2d1517}
  .hl{outline:3px solid #f0b72f;outline-offset:6px;border-radius:6px}
  .tg{width:760px;background:#0e1621;border-radius:12px;padding:26px;align-self:flex-start}
  .tg .msg{background:#182533;border-radius:14px;padding:16px 20px;white-space:pre-wrap;font-size:18px;color:#f5f5f5}
  .tg .from{color:#6ab3f3;font-weight:600;margin-bottom:6px}
  .tg .keys{display:flex;gap:8px;margin-top:8px}
  .tg .keys div{flex:1;text-align:center;padding:12px;border-radius:10px;background:#2b5278;font-weight:600}
  .tg .edited{color:#6d8399;font-size:14px;text-align:right;margin-top:6px}
  .tap{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;
    background:#fff8;pointer-events:none}`;

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const inline = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');

// Just enough Markdown for the judge's own comment: headings, a diff fence, bullets, a table.
export function markdown(md) {
    const out = [];
    let fence = null;
    for (const line of md.split('\n')) {
        if (line.startsWith('```')) {
            if (fence === null) { fence = []; continue; }
            out.push(`<pre class="hl">${fence.join('\n')}</pre>`); fence = null; continue;
        }
        if (fence !== null) {
            const cls = line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
            fence.push(`<span class="${cls}">${esc(line)}</span>`); continue;
        }
        if (line.startsWith('|---')) continue;
        if (line.startsWith('|')) {
            const cells = line.split(/(?<!\\)\|/).slice(1, -1).map((c) => `<td>${inline(c.trim().replace(/\\\|/g, '|'))}</td>`);
            out.push(`<tr>${cells.join('')}</tr>`); continue;
        }
        const h = /^(#{2,3}) (.*)/.exec(line);
        if (h) out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
        else if (line.startsWith('* ')) out.push(`<li>${inline(line.slice(2))}</li>`);
        else if (/^_.*_$/.test(line)) out.push(`<p><i>${inline(line.slice(1, -1))}</i></p>`);
        else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
    return out.join('').replace(/(<tr>.*<\/tr>)/s, '<table>$1</table>');
}

export function ttyLine(line) {
    if (line.startsWith('$ ')) return `<span class="p">$</span> ${esc(line.slice(2))}`;
    if (/^(merge REFUSED|⛔)/.test(line)) return `<span class="bad">${esc(line)}</span>`;
    if (/^(✅|→|⏳)/.test(line)) return `<span class="ok">${esc(line)}</span>`;
    if (line.startsWith('+')) return `<span class="ok">${esc(line)}</span>`;
    return `<span class="dim">${esc(line)}</span>`;
}

const dots = '<b style="background:#ff5f57"></b><b style="background:#febc2e"></b><b style="background:#28c840"></b>';

async function main(scenesPath, outDir) {
    const scenes = JSON.parse(readFileSync(scenesPath, 'utf8'));
    const dir = join(outDir, 'gate.frames');
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
    await page.setContent(`<style>${CSS}</style><div id="cap"></div><div id="stage"></div>`);
    const list = [];
    const said = [];
    const shot = async (seconds) => {
        const file = `f${String(list.length / 2).padStart(5, '0')}.png`;
        await page.screenshot({ path: join(dir, file) });
        list.push(`file '${file}'`, `duration ${seconds.toFixed(3)}`);
        said.push(await page.evaluate((f) => ({ file: f, text: document.body.innerText,
            tap: document.querySelectorAll('.tap').length }), file));
    };
    const show = (cap, html) => page.evaluate(([c, h]) => {
        document.querySelector('.tap')?.remove();
        document.getElementById('cap').textContent = c;
        document.getElementById('stage').innerHTML = h;
    }, [cap, html]);

    for (const s of scenes) {
        if (s.kind === 'term') {
            const done = [];
            const render = (extra = '') => show(s.caption, `<div class="win"><div class="bar">${dots}${esc(s.title)}</div>`
                + `<pre class="tty">${[...done, extra].join('\n')}</pre></div>`);
            for (const line of s.lines) {
                if (line.startsWith('$ ')) {
                    for (let i = 2; i <= line.length; i += 2) { await render(ttyLine(line.slice(0, i))); await shot(0.045); }
                    await shot(0.4);
                    done.push(ttyLine(line));
                } else { done.push(ttyLine(line)); }
            }
            await render();
            await shot(Math.min(7, 2.5 + 0.3 * s.lines.length));
        } else if (s.kind === 'comment') {
            await show(s.caption, `<div class="win gh"><div class="bar">chela judge commented on PR #42</div>`
                + `<div class="md" id="md">${markdown(s.body)}</div></div>`);
            await shot(3);
            const target = await page.evaluate(() => {
                const md = document.getElementById('md');
                const hl = md.querySelector('pre, table');
                return hl ? Math.max(0, Math.min(hl.offsetTop - md.clientHeight / 2, md.scrollHeight - md.clientHeight)) : 0;
            });
            for (let y = 0; y < target; y += 24) { await page.evaluate((v) => { document.getElementById('md').scrollTop = v; }, y); await shot(0.04); }
            await page.evaluate((v) => { document.getElementById('md').scrollTop = v; }, target);
            await shot(6);
        } else if (s.kind === 'telegram') {
            const card = (text, keys) => `<div class="tg"><div class="msg"><div class="from">chela</div>${esc(text)}`
                + (keys ? '' : '<div class="edited">edited</div>') + '</div>'
                + (keys ? '<div class="keys"><div id="approve">✅ Approve</div><div>❌ Deny</div></div>' : '') + '</div>';
            await show(s.caption, card(s.text, true));
            await shot(4);
            const box = await page.locator('#approve').boundingBox();
            for (const [r, a] of [[0.5, 1], [1, 0.7], [1.5, 0.35]]) {
                await page.evaluate(([x, y, r2, a2]) => {
                    let t = document.querySelector('.tap');
                    if (!t) { t = document.createElement('div'); t.className = 'tap'; document.body.appendChild(t); }
                    Object.assign(t.style, { left: `${x}px`, top: `${y}px`, transform: `scale(${r2})`, opacity: a2 });
                }, [box.x + box.width / 2, box.y + box.height / 2, r, a]);
                await shot(0.08);
            }
            await show(s.caption, card(s.after, false));
            await shot(5);
        }
    }
    list.push(list[list.length - 2]);   // concat demuxer: the last file again, so its duration counts
    writeFileSync(join(dir, 'frames.txt'), list.join('\n') + '\n');
    writeFileSync(join(dir, 'text.json'), JSON.stringify(said, null, 1));
    await browser.close();
    console.log(`gate: ${list.length / 2 | 0} frames → ${dir}`);
}

// Imported (the tests do, for markdown / ttyLine), it only defines; run, it renders.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
    const [scenesPath, outDir] = process.argv.slice(2);
    if (!scenesPath || !outDir) {
        console.error('usage: gate.mjs <scenes.json> <out-dir>');
        process.exit(2);
    }
    main(scenesPath, outDir).catch((e) => { console.error(e); process.exit(1); });
}
