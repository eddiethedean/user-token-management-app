/* Playwright acceptance for Team promotion. Uses a disposable loopback app and database. */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { setTimeout: delay } = require('node:timers/promises');
const { chromium } = require('playwright');

const serveFixture = String.raw`
import socket
import sys

import uvicorn
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Role, User, utcnow
from app.schema import upgrade_schema
from app.security.passwords import PasswordService
from app.services.auth_common import ensure_default_roles

database_path, password = sys.argv[1:]
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_mover_mode="demo",
        database_url=f"sqlite:///{database_path}",
        public_base_url=base_url,
        authentication_mode="local_password",
        allowed_email_domains="example.gov",
        log_level="ERROR",
    )
    app = create_app(settings)
    upgrade_schema(database_url=settings.database_url)
    with app.state.composition.database.session() as db:
        ensure_default_roles(db)
        password_hash = PasswordService(settings).hash(password)
        roles = {role.name: role for role in db.scalars(select(Role))}
        for label in ("admin", "stale", "retry", "network", "fallback"):
            email = f"promotion-{label}@example.gov"
            db.add(User(
                email=email, email_original=email, status="active",
                email_verified_at=utcnow(), password_hash=password_hash,
                roles=[roles["administrator" if label == "admin" else "user"]],
            ))
        db.commit()
    print(f"PROMOTION_BROWSER_URL={base_url}", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[listener])
`;

const root = path.resolve(__dirname, '..');
const actionPattern = '**/admin/users/*/promote';
const accounts = Object.fromEntries(['stale', 'retry', 'network', 'fallback'].map(
  label => [label, `promotion-${label}@example.gov`],
));

function waitForBase(server) {
  return new Promise((resolve, reject) => {
    const output = createInterface({ input: server.stdout });
    const timeout = setTimeout(() => reject(new Error('Browser fixture did not start')), 20000);
    server.once('error', reject);
    server.once('exit', code => {
      clearTimeout(timeout);
      reject(new Error(`Browser fixture exited with code ${code}`));
    });
    output.on('line', line => {
      if (!line.startsWith('PROMOTION_BROWSER_URL=')) return;
      clearTimeout(timeout);
      resolve(line.slice('PROMOTION_BROWSER_URL='.length));
    });
  });
}

async function holdResponse(page, matcher) {
  let fetched;
  let release;
  let delivered;
  const ready = new Promise(resolve => { fetched = resolve; });
  const paused = new Promise(resolve => { release = resolve; });
  const done = new Promise(resolve => { delivered = resolve; });
  await page.route(matcher, async route => {
    try {
      const response = await route.fetch();
      fetched(response.status());
      await paused;
      await route.fulfill({ response });
      delivered(null);
    } catch (error) {
      fetched(null);
      delivered(error);
    }
  }, { times: 1 });
  return { ready, release, done };
}

function withinTestDeadline(promise, message) {
  return Promise.race([promise, delay(6500, null, { ref: false }).then(() => { throw new Error(message); })]);
}

async function check(browser, base, password) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const dialog = page.getByRole('dialog', { name: 'Make administrator' });
  const confirm = dialog.getByRole('button', { name: 'Make administrator', exact: true });
  const cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
  const feedback = dialog.locator('#promotion-dialog-feedback');
  const opener = email => page.locator(`[data-promotion-user-label="${email}"]`);
  const focused = selector => page.waitForFunction(value => document.activeElement?.matches(value), selector);
  const posted = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/promote')) {
      posted.push(new URL(request.url()).pathname);
    }
  });
  const open = async email => {
    await opener(email).focus();
    await opener(email).press('Enter');
    await dialog.waitFor({ state: 'visible' });
    assert.equal(await dialog.locator('[data-promotion-account-label]').textContent(), email);
    await focused('[data-promotion-cancel]');
  };
  const close = async () => {
    await cancel.click();
    await dialog.waitFor({ state: 'hidden' });
  };
  const expectDialogError = async (code, retryable) => {
    const summary = `[data-promotion-error="${code}"]`;
    await feedback.locator(summary).waitFor({ state: 'visible' });
    await focused(summary);
    assert.equal(await confirm.isEnabled(), retryable);
  };
  const stub = (status, code, retryable) => ({
    status,
    contentType: 'text/html',
    headers: { 'HX-Retarget': '#promotion-dialog-feedback', 'HX-Reswap': 'innerHTML' },
    body: `<div tabindex="-1" data-promotion-error="${code}" data-promotion-retryable="${retryable}">` +
      '<p role="alert">Promotion could not be completed.</p></div>',
  });

  await page.goto(`${base}/login?next=%2Fadmin%2Fusers`);
  await page.locator('[name="email"]').fill('promotion-admin@example.gov');
  await page.locator('[name="password"]').fill(password);
  await page.getByRole('button', { name: 'Continue to workspace', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/admin/users');
  await open(accounts.stale);
  await cancel.press('Enter');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(posted.length, 0);

  // The directory can become stale while the dialog is open.
  await open(accounts.stale);
  const action = await opener(accounts.stale).getAttribute('data-promotion-action');
  const csrf = await dialog.locator('[name="csrf_token"]').inputValue();
  const disabled = await context.request.post(base + action.replace(/promote$/, 'toggle'), {
    form: { csrf_token: csrf }, maxRedirects: 0,
  });
  assert.equal(disabled.status(), 303);
  await confirm.press('Enter');
  await expectDialogError('target_ineligible', false);
  await close();
  await open(accounts.stale);
  assert.equal(await confirm.isDisabled(), true);
  await close();

  await open(accounts.retry);
  assert.equal(await feedback.textContent(), '');
  assert.equal(await confirm.isEnabled(), true);
  const retryPath = await opener(accounts.retry).getAttribute('data-promotion-action');
  await page.route(actionPattern, route => route.fulfill(stub(503, 'administrator_role_unavailable', false)), { times: 1 });
  await confirm.press('Enter');
  await expectDialogError('administrator_role_unavailable', false);
  assert.equal(posted.at(-1), retryPath);
  await close();

  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(`${base}/admin/users?q=promotion-&status=active`);
  await open(accounts.retry);
  const box = await dialog.boundingBox();
  assert.ok(box && box.x >= 0 && box.x + box.width <= 375, 'Dialog must fit a narrow viewport');
  let releaseRequest;
  let requestStarted;
  const waiting = new Promise(resolve => { requestStarted = resolve; });
  const paused = new Promise(resolve => { releaseRequest = resolve; });
  await page.route(actionPattern, async route => {
    requestStarted();
    await paused;
    await route.fulfill(stub(409, 'write_conflict', true));
  }, { times: 1 });
  const beforeRetry = posted.length;
  await confirm.press('Enter');
  try {
    await withinTestDeadline(waiting, 'Promotion POST did not start');
    assert.equal(await confirm.isDisabled(), true);
    await dialog.locator('[data-promotion-form]').evaluate(form => form.requestSubmit());
  } finally {
    releaseRequest();
  }
  await expectDialogError('write_conflict', true);
  assert.equal(posted.length, beforeRetry + 1);
  const retryId = await opener(accounts.retry).getAttribute('data-promotion-user-id');
  await close();

  // Deliver an earlier search snapshot while the promotion POST is pending.
  const search = await holdResponse(page, url =>
    url.pathname === '/admin/users' && url.searchParams.get('q') === 'promotion-retry',
  );
  let postStarted;
  let releasePost;
  const postPending = new Promise(resolve => { postStarted = resolve; });
  const postPaused = new Promise(resolve => { releasePost = resolve; });
  const continuePromotion = async route => {
    postStarted();
    await postPaused;
    await route.continue().catch(() => {});
  };
  // Keep interception active when the one-shot search route finishes. Chromium
  // releases paused requests if Playwright removes the last active route.
  await page.route(actionPattern, continuePromotion);
  await page.evaluate(() => {
    function markSearchComplete(event) {
      if (event.detail.requestConfig?.elt.getAttribute('aria-label') !== 'Filter users') return;
      document.body.dataset.testSearchComplete = 'true';
      document.removeEventListener('htmx:afterRequest', markSearchComplete);
    }
    document.addEventListener('htmx:afterRequest', markSearchComplete);
    const toastHost = document.getElementById('hedron-toast');
    const observer = new MutationObserver(() => {
      if (!toastHost.textContent.includes('The account is now an administrator. They must sign in again.')) return;
      document.body.dataset.testPromotionAnnounced = 'true';
      observer.disconnect();
    });
    observer.observe(toastHost, { childList: true, subtree: true });
  });
  try {
    await page.locator('#user-query').fill('promotion-retry');
    assert.equal(await withinTestDeadline(search.ready, 'Search did not start'), 200);
    await open(accounts.retry);
    await confirm.press('Enter');
    await withinTestDeadline(postPending, 'Promotion POST did not start');
    search.release();
    assert.equal(await withinTestDeadline(search.done, 'Search response was not delivered'), null);
    await page.waitForFunction(() => document.body.dataset.testSearchComplete === 'true');
    assert.equal(await dialog.isVisible(), true);
    assert.equal(new URL(page.url()).searchParams.get('q'), 'promotion-');
  } finally {
    search.release();
    releasePost();
  }
  await dialog.waitFor({ state: 'hidden' });
  await page.unroute(actionPattern, continuePromotion);
  await focused(`#directory-account-${retryId}`);
  assert.equal(await opener(accounts.retry).count(), 0);
  assert.match(await page.locator(`#directory-account-${retryId}`).locator('xpath=ancestor::tr').innerText(), /Administrator, user/);
  await page.waitForFunction(() => document.body.dataset.testPromotionAnnounced === 'true');
  assert.equal(posted.length, beforeRetry + 2);
  assert.equal(new URL(page.url()).searchParams.get('status'), 'active');

  // Exercise the real dependency error and the page feedback focus fallback.
  await open(accounts.network);
  await dialog.locator('[name="csrf_token"]').evaluate(input => { input.value = 'invalid'; });
  await confirm.press('Enter');
  await dialog.waitFor({ state: 'hidden' });
  await focused('#request-feedback');
  const pageFeedback = page.locator('#request-feedback');
  assert.match(await pageFeedback.innerText(), /Invalid CSRF token/);

  await open(accounts.fallback);
  await page.route(actionPattern, route => route.fulfill({
    status: 502, contentType: 'text/plain', body: 'Unavailable',
    headers: { 'X-Support-Reference': 'browser-proxy-reference' },
  }), { times: 1 });
  await confirm.press('Enter');
  await dialog.waitFor({ state: 'hidden' });
  await focused('#request-feedback');
  const proxyFeedback = await pageFeedback.innerText();
  assert.match(proxyFeedback, /Refresh the Team directory/);
  assert.match(proxyFeedback, /browser-proxy-reference/);
  assert.doesNotMatch(proxyFeedback, /Invalid CSRF token/);

  await page.reload();
  await open(accounts.network);
  // Keep the request open and shorten the configured deadline for this browser check.
  await page.evaluate(() => {
    function shortenPromotionTimeout(event) {
      if (!event.detail.elt.matches('form[data-promotion-form]')) return;
      document.body.dataset.testPromotionTimeout = String(event.detail.timeout);
      event.detail.timeout = 250;
      document.removeEventListener('htmx:configRequest', shortenPromotionTimeout);
    }
    document.addEventListener('htmx:configRequest', shortenPromotionTimeout);
  });
  let releaseTimeout;
  const stalled = new Promise(resolve => { releaseTimeout = resolve; });
  await page.route(actionPattern, async route => {
    await stalled;
    await route.abort().catch(() => {});
  }, { times: 1 });
  try {
    await confirm.press('Enter');
    await feedback.getByText('Refresh the Team directory to check whether the account was promoted.').waitFor();
    await focused('#promotion-dialog-feedback [role="alert"]');
    assert.equal(await page.locator('body').getAttribute('data-test-promotion-timeout'), '45000');
    assert.equal(await confirm.isDisabled(), true);
    await close();
  } finally {
    releaseTimeout();
  }

  // Let the server finish the operation, then drop the response to the browser.
  await page.reload();
  await open(accounts.network);
  await page.route(actionPattern, async route => {
    assert.equal((await route.fetch()).status(), 200);
    await route.abort('failed');
  }, { times: 1 });
  const beforeLoss = posted.length;
  await confirm.press('Enter');
  await feedback.getByText('Refresh the Team directory to check whether the account was promoted.').waitFor();
  await focused('#promotion-dialog-feedback [role="alert"]');
  assert.equal(await confirm.isDisabled(), true);
  assert.equal(posted.length, beforeLoss + 1);
  await close();
  await page.reload();
  assert.equal(await opener(accounts.network).count(), 0);

  // With the HTMX script absent, the initialized form still posts and follows a 303.
  await page.route('**/hedron-static/htmx.min.js*', route => route.abort());
  await page.reload();
  await open(accounts.fallback);
  await confirm.press('Enter');
  await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('notice') === 'role-updated');
  assert.equal(await opener(accounts.fallback).count(), 0);
  await page.unroute('**/hedron-static/htmx.min.js*');

  // Return to the account directory after session expiry without following the POST URL.
  await page.goto(`${base}/admin/users`);
  // The stale test account is disabled. Enable it for the session-expiry case.
  const enable = await context.request.post(base + action.replace(/promote$/, 'toggle'), {
    form: { csrf_token: csrf }, maxRedirects: 0,
  });
  assert.equal(enable.status(), 303);
  await page.reload();
  await open(accounts.stale);
  await context.clearCookies();
  await confirm.press('Enter');
  await page.waitForURL(url => url.pathname === '/login' && url.searchParams.get('next') === '/admin/users');
  await context.close();
  console.log('Promotion browser checks passed: keyboard, narrow layout, search overlap, conflict, unavailable role, CSRF, proxy feedback, timeout, dropped response, plain form, and expired session.');
}

async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'admin-promotion-browser-'));
  const database = path.join(directory, 'fixture.db');
  const password = `Fixture!${randomBytes(18).toString('hex')}`;
  const server = spawn(process.env.PYTHON || path.join(root, '.venv/bin/python'), ['-c', serveFixture, database, password], {
    cwd: root,
    env: { ...process.env, APP_ENV: 'test', DATA_MOVER_MODE: 'demo', LOG_LEVEL: 'ERROR', DATABASE_URL: `sqlite:///${database}` },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  const exited = once(server, 'close');
  let browser;
  try {
    const base = await waitForBase(server);
    let ready = false;
    for (let attempt = 0; !ready && attempt < 50; attempt++) {
      try { ready = (await fetch(`${base}/ready`, { signal: AbortSignal.timeout(500) })).ok; } catch { /* startup */ }
      if (!ready) await delay(100);
    }
    assert.equal(ready, true, 'Fixture did not become ready');
    browser = await chromium.launch();
    await check(browser, base, password);
  } finally {
    if (browser) await browser.close();
    server.kill();
    await exited.catch(() => {});
    await fs.rm(directory, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
