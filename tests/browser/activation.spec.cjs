/** Activation must fail closed on every page, including an installed app update. */
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { chromium, expect } = require('playwright/test');

const webRoot = path.resolve(__dirname, '../../src/options/web');
let browser;
before(async () => {
  browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
});
after(async () => { await browser?.close(); });

async function fixture(t, view, { activated = false, failStatus = false, statusValue } = {}) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  t.after(() => context.close());
  const page = await context.newPage();
  const state = { activated, failStatus, protectedReads: 0, submissions: 0, errors: [] };
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.route('http://options.test/**', async (route) => {
    const url = new URL(route.request().url());
    const json = (body, status = 200) => route.fulfill({ json: body, status });
    if (url.pathname === '/api/activation/status') {
      return state.failStatus ? json({ detail: 'unavailable' }, 503)
        : json({ activated: statusValue === undefined ? state.activated : statusValue });
    }
    if (url.pathname === '/api/activation') {
      state.submissions += 1;
      if (route.request().postDataJSON().code !== 'valid-test-code') return json({ detail: 'کد پذیرفته نشد' }, 400);
      state.activated = true;
      return json({ activated: true });
    }
    if (url.pathname.startsWith('/api/')) {
      state.protectedReads += 1;
      if (!state.activated) return json({ detail: 'activation_required' }, 423);
      const responses = {
        '/api/watchlist': { ins_codes: [] },
        '/api/summary': { underlying_count: 0, contract_count: 0 },
        '/api/underlyings': { items: [], total: 0, watchlist_count: 0 },
        '/api/dates': { items: [], latest: null },
        '/api/refresh/status': { running: false },
        '/api/underlyings/catalog': { items: [], refresh: { running: false } },
        '/api/expiries': { items: [], total: 0, refresh: { running: false } },
        '/api/calendar/today': { gregorian_date: '2026-10-24' },
      };
      return json(responses[url.pathname] || { items: [] });
    }
    const file = url.pathname.startsWith('/static/') ? url.pathname.slice(1)
      : url.pathname.endsWith('/symbols') ? 'symbols.html' : 'index.html';
    const mime = { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.ttf': 'font/ttf' };
    return route.fulfill({ body: await readFile(path.join(webRoot, file)), contentType: mime[path.extname(file)] });
  });
  const open = () => page.goto(`http://options.test/dashboard/test${view === 'symbols' ? '/symbols' : ''}`);
  t.after(() => assert.deepEqual(state.errors, []));
  return { page, state, open };
}

for (const view of ['dashboard', 'symbols']) {
  test(`${view}: an unactivated install still requires a valid code after update/reload`, async (t) => {
    const { page, state, open } = await fixture(t, view);
    await open();
    await expect(page.locator('#activationOverlay')).toBeVisible();
    await expect(page.locator('#activationCode')).toBeFocused();
    assert.equal(state.protectedReads, 0);
    await page.evaluate(() => { localStorage.setItem('options-theme', 'dark'); localStorage.setItem('activated', 'true'); });
    await page.reload();
    await expect(page.locator('#activationOverlay')).toBeVisible();
    assert.equal(state.protectedReads, 0);
    await page.locator('#activationCode').fill('wrong-code');
    await page.locator('#activationSubmit').click();
    await expect(page.locator('#activationError')).toHaveText('کد پذیرفته نشد');
    await expect(page.locator('#activationOverlay')).toBeVisible();
    assert.equal(state.activated, false);
    assert.equal(state.protectedReads, 0);
    await page.locator('#activationCode').fill('valid-test-code');
    await page.locator('#activationSubmit').click();
    await expect(page.locator('#activationOverlay')).toBeHidden();
    await expect.poll(() => state.protectedReads).toBeGreaterThan(0);
    assert.equal(state.submissions, 2);
  });

  test(`${view}: a previously activated user stays unlocked after reload`, async (t) => {
    const { page, state, open } = await fixture(t, view, { activated: true });
    await open();
    await expect(page.locator('#activationOverlay')).toBeHidden();
    await page.reload();
    await expect(page.locator('#activationOverlay')).toBeHidden();
    assert.equal(state.submissions, 0);
    await expect.poll(() => state.protectedReads).toBeGreaterThan(0);
  });

  test(`${view}: an activation-status failure cannot leave the interface unlocked`, async (t) => {
    const { page, state, open } = await fixture(t, view, { failStatus: true });
    await open();
    await expect(page.locator('#activationOverlay')).toBeVisible();
    await expect(page.locator('#activationError')).toContainText('بررسی وضعیت فعال‌سازی');
    assert.equal(state.protectedReads, 0);
  });

  test(`${view}: a malformed truthy activation status never unlocks the app`, async (t) => {
    const { page, state, open } = await fixture(t, view, { statusValue: 'false' });
    await open();
    await expect(page.locator('#activationOverlay')).toBeVisible();
    await expect(page.locator('#activationCode')).toBeFocused();
    assert.equal(state.protectedReads, 0);
  });

  test(`${view}: a locked API response restores the activation prompt`, async (t) => {
    const { page, state, open } = await fixture(t, view, { activated: true });
    await open();
    await expect(page.locator('#activationOverlay')).toBeHidden();
    state.activated = false;
    const status = await page.evaluate(async () => {
      try { await api('/api/watchlist'); } catch (error) { return error.status; }
    });
    assert.equal(status, 423);
    await expect(page.locator('#activationOverlay')).toBeVisible();
  });
}
