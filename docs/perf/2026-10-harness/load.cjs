// node load.cjs <port> <minutes> [tabs=wall,work,phone] — real-browser UI replay.
const { chromium, devices } = require(process.env.PW || 'playwright');
const [port, mins, tabsArg] = [process.argv[2], +process.argv[3], process.argv[4] || 'wall,work,phone'];
const base = `http://127.0.0.1:${port}/`;
(async () => {
  const browser = await chromium.launch({ headless: true });
  const specs = {
    wall:  { viewport: { width: 1600, height: 1000 }, view: null },
    work:  { viewport: { width: 1440, height: 900 }, view: 'work' },
    phone: { ...devices['iPhone 13'], view: null },
  };
  const pages = [];
  for (const name of tabsArg.split(',')) {
    const s = { ...specs[name] }; const view = s.view; delete s.view;
    const ctx = await browser.newContext({ ...s, extraHTTPHeaders: { 'X-Perf-Client': name } });
    const page = await ctx.newPage();
    page.on('pageerror', e => console.log(name, 'pageerror', String(e).slice(0, 160)));
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    if (view) { await page.waitForTimeout(3000); await page.evaluate(v => window.chela.selectView(v), view); }
    pages.push([name, page]);
    console.log(new Date().toISOString(), 'opened', name);
  }
  const end = Date.now() + mins * 60e3;
  while (Date.now() < end) await new Promise(r => setTimeout(r, 30e3));
  for (const [n, p] of pages) console.log(n, 'frames', p.frames().length);
  await browser.close();
})();
