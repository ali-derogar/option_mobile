/** Run with npm test --prefix tests/browser; CHROME_PATH can select system Chrome. */
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { readFile } = require("node:fs/promises");
const path = require("node:path");
const { chromium, expect } = require("playwright/test");

const webRoot = path.resolve(__dirname, "../../src/options/web");
let browser;
before(async () => {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  });
});
after(async () => { await browser?.close(); });

function snapshot(days = 10, changes = {}) {
  return {
    items: [{
      ins_code: "12345678901234567", symbol: "ضخود۸۰۱", underlying_symbol: "خودرو",
      option_type: "call", strike_price: 1200, end_date: 20261031, days_remaining: days,
    }],
    total: 1, fetched_at: "2026-10-21T09:00:00Z", as_of_date: "2026-10-21",
    nearest_days: days, warning: null, refresh: { running: false, last_error: null },
    ...changes,
  };
}

async function fixture(t, { now = "2026-10-21T12:00:00Z", initial = null } = {}) {
  // The client is deliberately outside Iran: day changes must still use Tehran.
  const context = await browser.newContext({ viewport: { width: 360, height: 800 }, timezoneId: "America/New_York" });
  t.after(() => context.close());
  const page = await context.newPage();
  await page.clock.install({ time: new Date(now) });
  const state = {
    payload: initial || snapshot(null, { items: [], total: 0, fetched_at: null }),
    getCalls: 0, postCalls: 0, postStatus: "started", errors: [],
  };
  page.on("pageerror", (error) => state.errors.push(error.message));
  await page.route("http://options.test/**", async (route) => {
    const url = new URL(route.request().url());
    const json = (data) => route.fulfill({ json: data });
    switch (url.pathname) {
      case "/api/activation/status": return json({ activated: true });
      case "/api/expiries": {
        state.getCalls += 1;
        if (state.failReads) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
        return json(state.getHandler ? await state.getHandler(state.getCalls) : state.payload);
      }
      case "/api/expiries/refresh": {
        state.postCalls += 1;
        if (state.failPost) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
        if (state.postHandler) return json(await state.postHandler());
        if (state.postStatus !== "cooldown") state.payload.refresh = { running: true };
        return json({ status: state.postStatus });
      }
      case "/api/dates": return json({ items: [], latest: null });
      case "/api/calendar/today": return json({ gregorian_date: "2026-10-21" });
      case "/api/calendar/1405/7/events": return json({ year: 1405, month: 7, days: [], events: [], holidays: [], events_available: false });
      case "/api/summary": return json({ underlying_count: 0, contract_count: 0 });
      case "/api/underlyings": return json({ items: [], total: 0, watchlist_count: 0 });
      case "/api/refresh/status": return json({ running: false, last_result: null });
    }
    const file = url.pathname.startsWith("/static/") ? url.pathname.slice(1) : "index.html";
    const mime = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".ttf": "font/ttf" };
    return route.fulfill({ body: await readFile(path.join(webRoot, file)), contentType: mime[path.extname(file)] });
  });
  state.open = async () => {
    await page.goto("http://options.test/dashboard/test");
    await expect(page.locator("#btnExpiries")).toBeVisible();
    await expect(page.locator("#loadingOverlay")).toHaveClass(/hidden/);
    await expect.poll(() => state.getCalls).toBeGreaterThan(0);
  };
  t.after(() => assert.deepEqual(state.errors, []));
  return { page, state };
}

test("opening the initial panel never fetches the market and leaves the badge hidden", async (t) => {
  const { page, state } = await fixture(t);
  await state.open();
  await expect(page.locator("#expiryPanel")).toBeHidden();
  await expect(page.locator("#expiryBadge")).toBeHidden();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryStatus")).toContainText("ابتدا سررسیدها");
  await expect(page.locator("#btnExpiries")).toHaveAttribute("aria-expanded", "true");
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryPanel")).toBeHidden();
  assert.equal(state.postCalls, 0);
});

for (const [days, tone, label] of [[10, "safe", "۱۰ روز"], [6, "safe", "۶ روز"], [5, "soon", "۵ روز"], [2, "soon", "۲ روز"], [1, "urgent", "۱ روز"], [0, "urgent", "امروز"]]) {
  test(`a cached ${days}-day expiry has the correct warning without opening the panel`, async (t) => {
    const { page, state } = await fixture(t, { initial: snapshot(days) });
    await state.open();
    await expect(page.locator("#expiryBadge")).toHaveText(label);
    await expect(page.locator("#expiryBadge")).toHaveClass(new RegExp(`expiry-${tone}`));
    await expect(page.locator("#expiryPanel")).toBeHidden();
    assert.equal(state.postCalls, 0);
  });
}

test("manual refresh runs with no selected symbols and finishes while the panel is closed", async (t) => {
  const { page, state } = await fixture(t);
  await state.open();
  await page.locator("#btnExpiries").click();
  // Two immediate events must still reserve only one refresh.
  await page.locator("#btnExpiryRefresh").evaluate((button) => { button.click(); button.click(); });
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  assert.equal(state.postCalls, 1);
  await page.locator("#btnExpiries").click();
  state.payload = snapshot(1);
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#expiryPanel")).toBeHidden();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryList li")).toHaveCount(1);
  await expect(page.locator("#expiryLastUpdate")).toContainText("آخرین دریافت");
  await page.reload();
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#expiryPanel")).toBeHidden();
  assert.equal(state.postCalls, 1);
});

test("Tehran midnight changes the warning and rows without a market refresh", async (t) => {
  const { page, state } = await fixture(t, { now: "2026-10-21T20:29:30Z", initial: snapshot(1) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  const previousCalls = state.getCalls;
  state.payload = snapshot(0, { as_of_date: "2026-10-22" });
  state.payload.items[0].symbol = "قرارداد بعدی";
  await page.clock.fastForward(61000);
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  assert(state.getCalls > previousCalls);
  assert.equal(state.postCalls, 0);
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryList")).toContainText("قرارداد بعدی");
});

test("an empty future snapshot has a neutral badge and an explicit empty message", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(null, { items: [], total: 0 }) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("بدون سررسید");
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryStatus")).toHaveText("سررسید پیش‌رو موجود نیست");
  await expect(page.locator("#expiryList li")).toHaveCount(0);
  assert.equal(state.postCalls, 0);
});

test("a failed refresh preserves cached rows and displays the failure", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await page.locator("#btnExpiries").click();
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = snapshot(10, { refresh: { running: false, last_error: "دریافت ناموفق بود؛ اطلاعات قبلی حفظ شد." } });
  await page.clock.fastForward(3100);
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryWarning")).toContainText("اطلاعات قبلی حفظ شد");
  await expect(page.locator("#expiryList li")).toHaveCount(1);
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
});

test("temporary status failures stop after three retries and allow resuming the job", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await page.locator("#btnExpiries").click();
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.failReads = true;
  for (let step = 0; step < 3; step += 1) {
    const calls = state.getCalls;
    await page.clock.fastForward(3100);
    await expect.poll(() => state.getCalls).toBeGreaterThan(calls);
  }
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryList li")).toHaveCount(1);
  await expect(page.locator("#expiryWarning")).toBeVisible();
  state.failReads = false;
  state.postStatus = "already_running";
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = snapshot(5);
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryBadge")).toHaveText("۵ روز");
});

test("a cooldown or failed POST leaves the refresh button usable", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await page.locator("#btnExpiries").click();
  state.postStatus = "cooldown";
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#expiryWarning")).toContainText("یک دقیقه");
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  state.failPost = true;
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#expiryWarning")).toContainText("شروع به‌روزرسانی");
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
});

test("a slow older read cannot replace a newer snapshot", async (t) => {
  const { page, state } = await fixture(t);
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(snapshot(10)));
  state.getHandler = (call) => call === 1 ? pending : snapshot(1);
  await state.open();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/expiries"));
  release(snapshot(10));
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
});

test("restoring a cached page ignores reads from its previous visit", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(snapshot(10)));
  const previousCalls = state.getCalls;
  state.getHandler = (call) => call === previousCalls + 1 ? pending : snapshot(1);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.getCalls).toBe(previousCalls + 1);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/expiries"));
  release(snapshot(10));
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
});

test("a focus event cannot orphan a pending manual refresh", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await page.locator("#btnExpiries").click();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release({ status: "started" }));
  state.postHandler = () => pending;
  await page.locator("#btnExpiryRefresh").click();
  await expect.poll(() => state.postCalls).toBe(1);
  const calls = state.getCalls;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  assert.equal(state.getCalls, calls);
  state.payload = snapshot(5);
  release({ status: "started" });
  await expect(page.locator("#expiryBadge")).toHaveText("۵ روز");
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
});

test("a lost start response reconciles a refresh already accepted by the server", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await page.locator("#btnExpiries").click();
  // The server accepted the mutation, but its response was lost in transit.
  state.payload = snapshot(10, { refresh: { running: true } });
  state.failPost = true;
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#expiryWarning")).toContainText("شروع به‌روزرسانی");
  await page.clock.fastForward(3100);
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = snapshot(1, { fetched_at: "2026-10-21T09:05:00Z" });
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#expiryWarning")).toBeHidden();
  assert.equal(state.postCalls, 1);
});

test("a rejected start keeps its failure visible after checking idle server state", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await page.locator("#btnExpiries").click();
  state.failPost = true;
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#expiryWarning")).toContainText("شروع به‌روزرسانی");
  const calls = state.getCalls;
  await page.clock.fastForward(3100);
  await expect.poll(() => state.getCalls).toBeGreaterThan(calls);
  await expect(page.locator("#expiryWarning")).toContainText("شروع به‌روزرسانی");
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  const idleCalls = state.getCalls;
  await page.clock.fastForward(10000);
  assert.equal(state.getCalls, idleCalls);
  assert.equal(state.postCalls, 1);
});

test("a delayed start response from a previous visit reloads current server state", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await page.locator("#btnExpiries").click();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release({ status: "started" }));
  state.postHandler = () => pending;
  await page.locator("#btnExpiryRefresh").click();
  await expect.poll(() => state.postCalls).toBe(1);
  const calls = state.getCalls;
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect.poll(() => state.getCalls).toBeGreaterThan(calls);
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  state.payload = snapshot(10, { refresh: { running: true } });
  release({ status: "started" });
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = snapshot(1);
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  assert.equal(state.postCalls, 1);
});

test("contract values render as text, Jalali dates fit the mobile layout in both themes", async (t) => {
  const data = snapshot(10);
  data.items[0].symbol = '<img src=x onerror="window.injected=true">';
  const { page, state } = await fixture(t, { initial: data });
  await state.open();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryList strong")).toHaveText(data.items[0].symbol);
  assert.equal(await page.locator("#expiryList img").count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await expect(page.locator("#expiryList")).toContainText("۱۴۰۵/۰۸/۰۹");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => document.documentElement.dataset.theme = value, theme);
    assert(await page.locator("#expirySection").evaluate((element) => element.scrollWidth <= element.clientWidth));
    assert(await page.locator("#expiryBadge").evaluate((element) => getComputedStyle(element).color !== getComputedStyle(element).backgroundColor));
  }
  assert.equal(state.postCalls, 0);
});
