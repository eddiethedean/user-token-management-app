/* Capture actual UI panels from docs_guardrail_fixture.py using Playwright.
 * NODE_PATH must include a Playwright installation. No DOM/CSS alterations.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const base = 'http://127.0.0.1:8876';
const output = path.resolve(__dirname, '../docs/screenshots/stories');
const captures = [];

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    await page.goto(`${base}/login`);
    await page.locator('[name=email]').fill('docs@example.gov');
    await page.locator('[name=password]').fill('Quartz-Beacon-62!Harbor');
    await page.getByRole('button', { name: 'Continue to workspace', exact: true }).click();
    await page.waitForURL(url => url.pathname !== '/login');
    await page.goto(`${base}/pipeline`);
    const footer = await page.locator('footer.hedron-app-footer').innerText();
    if (!footer.includes('Test environment') || !footer.includes('Transfers are simulated')) throw new Error('Start docs_guardrail_fixture.py before capturing');
    const appVersion = footer.match(/Version ([\d.]+)/)[1];
    const csrf = await page.locator('[name=csrf_token]').first().inputValue();
    const post = async (url, form, headers = {}) => {
      const result = await context.request.post(`${base}${url}`, { form: { csrf_token: csrf, ...form }, headers, maxRedirects: 0 });
      if (![200, 202, 303].includes(result.status())) throw new Error(`${url}: ${result.status()} ${(await result.text()).slice(0, 500)}`);
      return result;
    };
    const hx = { 'HX-Request': 'true', 'HX-Target': 'pipeline-preview-region' };
    const sourceKey = (provider, schema = '', table = '', upload = '') => crypto.createHash('sha256').update(JSON.stringify(provider === 'csv' ? [provider, upload] : [provider, schema, table])).digest('hex');
    const save = async (form, actions = []) => {
      const parameters = new URLSearchParams({ csrf_token: csrf, ...form });
      for (const action of actions) parameters.append('guardrail_actions', JSON.stringify(action));
      const result = await context.request.post(`${base}/pipeline/save`, { data: parameters.toString(), headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, maxRedirects: 0 });
      if (result.status() !== 303) throw new Error(`save ${result.status()}: ${await result.text()}`);
      return new URL(result.headers().location, base).searchParams.get('pipeline_id');
    };
    const capture = async (filename, locator, state) => {
      await locator.scrollIntoViewIfNeeded();
      await page.evaluate(() => document.fonts.ready);
      const filepath = path.join(output, filename);
      await locator.screenshot({ path: filepath, type: 'jpeg', quality: 95, animations: 'disabled' });
      const controls = await locator.locator('select').evaluateAll(elements => elements.map(element => ({ name: element.name, selected: element.selectedOptions[0]?.textContent })));
      const text = await locator.innerText();
      if (text.includes('123-45-6789') || text.includes('234-56-7890')) throw new Error('Matched fixture value leaked into a capture');
      captures.push({ filename, state, text, controls, sha256: crypto.createHash('sha256').update(fs.readFileSync(filepath)).digest('hex') });
      console.log(filename);
    };
    const guardrail = () => page.locator('.hedron-surface').filter({ has: page.getByRole('heading', { name: 'Sensitive-data guardrails', exact: true }) }).last();
    const openRoute = async id => {
      await page.goto(`${base}/pipeline?pipeline_id=${id}&notice=saved`);
      await page.getByRole('tab', { name: 'Target schema & creator', exact: true }).click();
      await guardrail().waitFor({ state: 'visible' });
    };
    const foundry = { pipeline_name: 'Metadata review example', source_provider: 'mss', source_schema: 'ri.foundry.main.dataset.demo-operations', source_table: 'mission_orders.parquet', destination_provider: 'postgres', destination_schema: 'public', destination_table: 'guardrail_metadata', write_mode: 'replace' };
    const metadataId = await save(foundry);
    await openRoute(metadataId);
    await capture('CDO-4552-metadata-findings.jpg', guardrail(), 'Before a run: two metadata findings, no choices yet');
    const metadataKey = sourceKey('mss', foundry.source_schema, foundry.source_table);
    const metadataActions = [['foundry_metadata', 'unit_name', 'hash', metadataKey], ['foundry_metadata', 'score', 'remove', metadataKey]];
    const selectedId = await save({ ...foundry, pipeline_id: metadataId }, metadataActions);
    await openRoute(selectedId);
    await capture('CDO-4559-metadata-actions.jpg', guardrail(), 'Saved metadata choices: score Remove, unit_name Hash; no transfer yet');
    const tableId = await save({ ...foundry, pipeline_name: 'Blocked table example', source_table: 'readiness_rollup.parquet', destination_table: 'guardrail_table_block' });
    await openRoute(tableId);
    await capture('CDO-4556-table-block.jpg', guardrail(), 'Pre-run table-level marking with disabled scan control');
    await post('/pipeline/runs', { pipeline_id: tableId });
    await page.goto(`${base}/pipeline?pipeline_id=${tableId}`);
    await capture('CDO-4556-blocked-run.jpg', guardrail(), 'Persisted table block: zero rows scanned, execution not started, destination unchanged');
    const rows = ['1,,public', ...Array.from({ length: 999 }, (_, i) => `${i + 2},ordinary,public`), '1001,123-45-6789,234-56-7890', '1002,123-45-6789,'];
    const csv = Buffer.from('id,ssn,alternate\n' + rows.join('\n') + '\n');
    const upload = await context.request.post(`${base}/pipeline/csv/inspect`, { multipart: { csrf_token: csrf, csv_file: { name: 'guardrail-story-fixture.csv', mimeType: 'text/csv', buffer: csv } }, headers: { 'HX-Request': 'true', 'HX-Target': 'pipeline-csv-inspection' } });
    if (upload.status() !== 200) throw new Error(`upload ${upload.status()}: ${await upload.text()}`);
    const uploadId = (await upload.text()).match(/name="source_upload_id" value="([^"]+)"/)[1];
    const contentForm = { pipeline_name: 'Content Hash and Remove example', source_provider: 'csv', source_upload_id: uploadId, destination_provider: 'postgres', destination_schema: 'public', destination_table: 'guardrail_content', write_mode: 'replace' };
    const contentId = await save(contentForm);
    await openRoute(contentId);
    await page.getByRole('button', { name: 'Scan source for SSNs', exact: true }).click();
    await guardrail().getByText('SSN scan complete', { exact: true }).waitFor();
    await capture('CDO-4563-content-scan.jpg', guardrail(), 'Completed preview scan: ssn 2 rows, alternate 1 row, no decisions');
    await post('/pipeline/runs', { pipeline_id: contentId });
    await page.goto(`${base}/pipeline?pipeline_id=${contentId}`);
    await capture('CDO-4567-unresolved-run.jpg', guardrail(), 'Unresolved findings block after the full scan: no actions, review required, destination unchanged');
    const contentKey = sourceKey('csv', '', '', uploadId);
    const contentActions = [['ssn', 'ssn', 'hash', contentKey], ['ssn', 'alternate', 'remove', contentKey]];
    await save({ ...contentForm, pipeline_id: contentId }, contentActions);
    await openRoute(contentId);
    await page.getByRole('button', { name: 'Scan source for SSNs', exact: true }).click();
    await guardrail().getByText('SSN scan complete', { exact: true }).waitFor();
    await capture('CDO-4567-content-actions.jpg', guardrail(), 'Saved choices after another preview scan: ssn Hash, alternate Remove');
    await post('/pipeline/runs', { pipeline_id: contentId });
    await page.goto(`${base}/pipeline?pipeline_id=${contentId}`);
    await capture('CDO-4571-transformed-run.jpg', guardrail(), 'Successful worker run: 1002 scanned rows, two applied actions, destination committed');
    const manifests = page.locator('details').filter({ has: page.locator('summary').getByText('Source and destination manifests', { exact: true }) });
    await manifests.locator('summary').click();
    await capture('CDO-4571-destination-schema.jpg', manifests, 'Persisted source has id, ssn, alternate; destination has id and ssn with alternate removed');
    const failedId = await save({ ...contentForm, pipeline_name: 'Failed preparation example', destination_table: 'guardrail_failed' }, contentActions);
    await post('/pipeline/runs', { pipeline_id: failedId });
    await page.goto(`${base}/pipeline?pipeline_id=${failedId}`);
    await capture('CDO-4575-failed-run.jpg', guardrail(), 'Destination preparation failure: decisions selected, transformation not started, destination unchanged');
    await page.goto(`${base}/admin/audit?event_type=pipeline.run.completed&outcome=success`);
    await page.getByText('View details', { exact: true }).first().click();
    const auditDetail = JSON.parse(await page.locator('#audit-results-region details code').first().innerText());
    if (auditDetail.run_status !== 'succeeded' || auditDetail.loaded_rows !== 1002) throw new Error('Unexpected completed-run audit event');
    fs.writeFileSync(path.join(output, 'audit-example.json'), JSON.stringify(auditDetail, null, 2) + '\n');
    await page.getByText('View details', { exact: true }).first().click();
    await capture('CDO-4575-run-audit.jpg', page.locator('#audit-results-region'), 'Audit event summary with filters, completed event, success badge, and collapsed detail control');
    fs.writeFileSync(path.join(output, 'capture-manifest.json'), JSON.stringify({ capturedAt: new Date().toISOString(), appVersion, mode: 'isolated synthetic demo, APP_ENV=test', viewport: { width: 1440, height: 1100 }, captures }, null, 2) + '\n');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
