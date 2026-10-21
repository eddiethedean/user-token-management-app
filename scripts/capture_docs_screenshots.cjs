/* Capture the real desktop UI from an isolated `make demo` instance.
 * See docs/screenshots/README.md. No DOM, CSS, or application-state overrides.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const os = require('node:os');
const { execFileSync } = require('node:child_process');

const base = process.env.DOCS_BASE_URL || 'http://127.0.0.1:8878';
const output = path.resolve(__dirname, '../docs/screenshots');
const captures = [];
const errors = [];

async function main() {
  if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use an isolated loopback demo');
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const ready = async () => {
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => !document.querySelector('.htmx-request'));
    };
    const capture = async (filename, state, locator) => {
      await ready();
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`Page overflow: ${filename}`);
      const filepath = path.join(output, filename);
      if (locator) {
        await locator.scrollIntoViewIfNeeded();
        await locator.screenshot({ path: filepath, type: 'jpeg', quality: 92, animations: 'disabled' });
      } else {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({ path: filepath, fullPage: true, type: 'jpeg', quality: 92, animations: 'disabled' });
      }
      captures.push({ filename, state, sha256: crypto.createHash('sha256').update(fs.readFileSync(filepath)).digest('hex') });
      console.log(filename);
    };
    // Login honors the same host-owned color-mode preference as the workspace.
    for (const mode of ['dark', 'light']) {
      await context.addCookies([{ name: 'data_mover_color_mode', value: mode, url: base }]);
      await page.goto(`${base}/login`);
      await capture(mode === 'dark' ? 'login.jpg' : 'login-light.jpg', `${mode} sign in; synthetic offline demo`);
    }
    await page.locator('[name=email]').fill(process.env.DOCS_EMAIL || 'docs@example.gov');
    await page.locator('[name=password]').fill(process.env.DOCS_PASSWORD || 'Quartz-Beacon-62!Harbor');
    await page.getByRole('button', { name: 'Continue to workspace', exact: true }).click();
    await page.waitForURL(url => url.pathname !== '/login');
    const footer = await page.locator('footer.hedron-app-footer').innerText();
    if (!footer.includes('Transfers are simulated')) throw new Error('Refusing to capture a live environment');
    const appVersion = footer.match(/Version ([\d.]+)/)[1];
    await page.goto(`${base}/pipeline`);
    await page.getByRole('tab', { name: 'Route setup', exact: true }).click();
    await page.locator('[name=destination_provider]').selectOption('databricks');
    await page.waitForFunction(() => document.querySelector('[name=destination_schema]')?.selectedOptions[0]?.textContent.includes('analytics'));
    const namespace = await page.locator('[name=destination_schema]').inputValue();
    const csrf = await page.locator('[name=csrf_token]').first().inputValue();
    const post = async (url, form) => {
      const result = await context.request.post(`${base}${url}`, { form: { csrf_token: csrf, ...form }, maxRedirects: 0 });
      if (result.status() !== 303) throw new Error(`${url}: ${result.status()} ${(await result.text()).slice(0, 500)}`);
      return result;
    };
    const saved = await post('/pipeline/save', {
      pipeline_name: 'Readiness to Delta', source_provider: 'postgres', source_schema: 'public',
      source_table: 'asset_inventory', destination_provider: 'databricks', destination_schema: namespace,
      destination_table: 'readiness_events', write_mode: 'replace',
    });
    const pipelineId = new URL(saved.headers().location, base).searchParams.get('pipeline_id');
    const queued = await post('/pipeline/runs', { pipeline_id: pipelineId });
    let liveUrl = new URL(queued.headers().location, base).href;
    await page.goto(liveUrl);
    await page.locator('[data-transfer-state=succeeded]').waitFor({ timeout: 60000 });
    if (!(await page.locator('#pipeline-run-monitor').innerText()).includes('3 rows')) throw new Error('Expected the three-row demo transfer');
    const setMode = async mode => {
      const form = { theme: 'folio', next: '/pipeline' };
      if (mode === 'dark') form.dark_mode = 'on';
      await post('/preferences/theme', form);
    };
    const record = async mode => {
      const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'data-mover-transfer-'));
      try {
        const response = await post('/pipeline/runs', { pipeline_id: pipelineId });
        liveUrl = new URL(response.headers().location, base).href;
        await page.goto(liveUrl);
        const route = page.locator('[data-transfer-route]');
        await route.waitFor();
        await route.scrollIntoViewIfNeeded();
        const clip = await route.evaluate(element => {
          const bounds = element.getBoundingClientRect();
          return { x: bounds.x + scrollX, y: bounds.y + scrollY, width: bounds.width, height: bounds.height };
        });
        const frames = [];
        let completed = 0;
        const start = Date.now();
        while (Date.now() - start < 60000 && completed < 8) {
          const state = await route.getAttribute('data-transfer-state');
          if (['failed', 'cancelled', 'blocked', 'failed_needs_reconciliation'].includes(state)) throw new Error(`Animation run stopped: ${state}`);
          const filename = path.join(directory, `${String(frames.length).padStart(4, '0')}.png`);
          const time = Date.now();
          // Capture a fixed page region so HTMX replacing the route cannot detach
          // an element screenshot halfway through a frame.
          await page.screenshot({ path: filename, clip });
          frames.push({ filename, state, time });
          if (state === 'succeeded') completed++;
          await page.waitForTimeout(100);
        }
        if (completed < 8) throw new Error('Animation never reached success');
        const states = [...new Set(frames.map(frame => frame.state))];
        if (!states.includes('loading')) throw new Error('Animation missed loading; rerun capture');
        fs.writeFileSync(path.join(directory, 'frames.json'), JSON.stringify(frames));
        const filename = `transfer-lifecycle-${mode}.gif`;
        execFileSync(process.env.DOCS_ANIMATION_PYTHON, ['-c', [
          'import json, sys',
          'from PIL import Image',
          'frames = json.load(open(sys.argv[1]))',
          'images = [Image.open(f["filename"]).convert("RGB") for f in frames]',
          'size = (max(i.width for i in images), max(i.height for i in images))',
          'canvases = []',
          'for image in images:',
          '    canvas = Image.new("RGB", size, image.getpixel((0, 0)))',
          '    canvas.paste(image, (0, 0))',
          '    canvases.append(canvas)',
          'durations = [max(20, frames[i+1]["time"]-f["time"]) if i+1 < len(frames) else 100 for i, f in enumerate(frames)]',
          'canvases[0].save(sys.argv[2], save_all=True, append_images=canvases[1:], duration=durations, loop=0, disposal=2)',
        ].join('\n'), path.join(directory, 'frames.json'), path.join(output, filename)]);
        captures.push({ filename, state: `${mode}; actual demo lifecycle through loading to success`,
          observedStates: states, frames: frames.length,
          sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(output, filename))).digest('hex') });
        console.log(filename, states.join(' → '));
      } finally {
        fs.rmSync(directory, { recursive: true, force: true });
      }
    };
    for (const mode of ['dark', 'light']) {
      await setMode(mode);
      if (process.env.DOCS_ANIMATION_PYTHON) await record(mode);
      const suffix = mode === 'dark' ? '' : '-light';
      await page.goto(liveUrl);
      await page.locator('[data-transfer-state=succeeded]').waitFor();
      await capture(`pipeline-live${suffix}.jpg`, `${mode}; succeeded PostgreSQL to Delta replace, 3 rows`);
      await capture(`transfer-complete-${mode}.jpg`, `${mode}; persisted completed transfer panel`, page.locator('[data-transfer-route]'));
      await page.goto(`${base}/pipeline?pipeline_id=${pipelineId}&notice=saved`);
      await capture(`navigation-expanded${suffix}.jpg`, `${mode}; saved PostgreSQL to Delta route, expanded navigation`);
      await page.getByRole('tab', { name: 'Target schema & creator', exact: true }).click();
      await capture(`schema-preview${suffix}.jpg`, `${mode}; target schema preview for saved Delta route`);
      await page.getByRole('tab', { name: 'Saved routes', exact: true }).click();
      await capture(`saved-routes${suffix}.jpg`, `${mode}; reusable demo routes`);
      await page.goto(`${base}/security`);
      await capture(`connection-credentials${suffix}.jpg`, `${mode}; encrypted credential forms, no saved plaintext`);
      await page.getByRole('tab', { name: 'Status', exact: true }).click();
      await page.locator('#connection-status-list').waitFor();
      const statusText = await page.locator('#connection-status-list').innerText();
      for (const label of ['MSS', 'MCS-COP', 'PostgreSQL', 'Databricks']) {
        if (!statusText.includes(label)) throw new Error(`Missing provider: ${label}`);
      }
      await capture(`connections-status${suffix}.jpg`, `${mode}; all four emulated providers, Databricks ready`);
      await page.goto(`${base}/profile`);
      await capture(`account${suffix}.jpg`, `${mode}; synthetic administrator profile`);
      await page.goto(`${base}/admin/users`);
      await capture(`team${suffix}.jpg`, `${mode}; isolated demo user directory`);
      await page.goto(`${base}/admin/audit?event_type=pipeline.run.completed&outcome=success`);
      await page.locator('#audit-results-region').waitFor();
      await capture(`audit-activity${suffix}.jpg`, `${mode}; filtered successful pipeline completion audit`);
    }
    if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);
    fs.writeFileSync(path.join(output, 'capture-manifest.json'), JSON.stringify({
      capturedAt: new Date().toISOString(), appVersion, mode: 'isolated synthetic demo',
      viewport: { width: 1440, height: 1000 }, captures,
    }, null, 2) + '\n');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
