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

function preview(days, processed, total, changes = {}) {
  return snapshot(days, {
    partial: true, fetched_at: null, preview_updated_at: "2026-10-21T09:01:00Z",
    refresh: { running: true, stage: "metadata", processed, total },
    ...changes,
  });
}

function analyticalSnapshot(changes = {}) {
  return snapshot(0, {
    items: [{
      ins_code: "12345678901234567", symbol: "ضخود۸۰۱", underlying_ins_code: "2001", underlying_symbol: "خودرو",
      option_type: "call", strike_price: 1000, end_date: 20261021, days_remaining: 0,
      ask_price: 100, ask_volume: 20, underlying_last_price: 1120, intrinsic_value: 120,
      intrinsic_gap_percent: 20, trade_volume: 100, moneyness: "ITM",
    }],
    total_upcoming: 2, market_received_at: "2026-10-21T09:00:00Z", pricing_current: true,
    underlyings: [{ ins_code: "2001", symbol: "خودرو" }, { ins_code: "2002", symbol: "فملی" }],
    ...changes,
  });
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
        return json(state.getHandler ? await state.getHandler(state.getCalls, url.searchParams) : state.payload);
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

test("incremental rows, badge and progress appear before the first full refresh completes", async (t) => {
  const { page, state } = await fixture(t);
  await state.open();
  await page.locator("#btnExpiries").click();
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  await expect(page.locator("#expiryProgress")).toBeVisible();
  await expect(page.locator("#expiryProgress")).not.toHaveAttribute("value");
  state.payload = preview(10, 1, 100);
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryList li")).toHaveCount(1);
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "1");
  await expect(page.locator("#expiryStatus")).toContainText("بررسی ۱ از ۱۰۰ قرارداد");
  await expect(page.locator("#expiryPreviewNote")).toBeVisible();
  await expect(page.locator("#expiryLastUpdate")).toContainText("نخستین دریافت");
  state.payload = preview(1, 50, 100);
  state.payload.items[0].symbol = "قرارداد اصلاح‌شده";
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "50");
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#expiryList")).toContainText("قرارداد اصلاح‌شده");
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = snapshot(1, { refresh: { running: false, stage: "complete", processed: 100, total: 100 } });
  await page.clock.fastForward(3100);
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryProgress")).toBeHidden();
  await expect(page.locator("#expiryPreviewNote")).toBeHidden();
  await expect(page.locator("#expiryLastUpdate")).toContainText("آخرین دریافت");
  assert.equal(state.postCalls, 1);
});

test("a partial refresh failure restores the previous complete rows and badge", async (t) => {
  const original = snapshot(10);
  const { page, state } = await fixture(t, { initial: original });
  await state.open();
  await page.locator("#btnExpiries").click();
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = preview(1, 25, 100, { fetched_at: original.fetched_at });
  state.payload.items[0].symbol = "نتیجه موقت";
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryList")).toContainText("نتیجه موقت");
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#expiryLastUpdate")).toContainText("آخرین دریافت کامل");
  state.payload = snapshot(10, { refresh: { running: false, stage: "failed", last_error: "آخرین دریافت کامل حفظ شد." } });
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await expect(page.locator("#expiryList")).not.toContainText("نتیجه موقت");
  await expect(page.locator("#expiryWarning")).toContainText("آخرین دریافت کامل حفظ شد");
  await expect(page.locator("#expiryPreviewNote")).toBeHidden();
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
});

test("restoring the page resumes incremental progress without another market request", async (t) => {
  const { page, state } = await fixture(t, { initial: preview(5, 10, 100) });
  await state.open();
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "10");
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  state.payload = preview(1, 80, 100);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "80");
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  assert.equal(state.postCalls, 0);
});

test("an empty preview does not falsely claim there are no future expiries", async (t) => {
  const data = preview(null, 1, 100, { items: [], total: 0, fetched_at: "2026-10-21T09:00:00Z" });
  const { page, state } = await fixture(t, { initial: data });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("در حال بررسی");
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryStatus")).toContainText("بررسی ۱ از ۱۰۰ قرارداد");
  await expect(page.locator("#expiryPreviewNote")).toBeVisible();
  await expect(page.locator("#expiryStatus")).not.toContainText("سررسید پیش‌رو موجود نیست");
});

test("retrying during cooldown reconciles a preview whose refresh finished while disconnected", async (t) => {
  const { page, state } = await fixture(t, { initial: preview(10, 10, 100) });
  await state.open();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryPreviewNote")).toBeVisible();
  state.failReads = true;
  for (let step = 0; step < 3; step += 1) {
    const calls = state.getCalls;
    await page.clock.fastForward(3100);
    await expect.poll(() => state.getCalls).toBeGreaterThan(calls);
  }
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  state.failReads = false;
  state.payload = snapshot(1, {
    fetched_at: "2026-10-21T09:05:00Z",
    refresh: { running: false, stage: "complete", processed: 100, total: 100 },
  });
  state.postStatus = "cooldown";
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#expiryWarning")).toContainText("یک دقیقه");
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryPreviewNote")).toBeHidden();
  await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
  await expect(page.locator("#expiryLastUpdate")).toContainText("آخرین دریافت");
  assert.equal(state.postCalls, 1);
});

test("a stalled status request times out and polling recovers without losing the preview", async (t) => {
  const { page, state } = await fixture(t, { initial: preview(10, 10, 100) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await page.locator("#btnExpiries").click();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const calls = state.getCalls;
  state.getHandler = (call) => call === calls + 1 ? pending : snapshot(1);
  try {
    await page.clock.fastForward(3100);
    await expect.poll(() => state.getCalls).toBe(calls + 1);
    await page.clock.fastForward(15000);
    await expect(page.locator("#expiryWarning")).toContainText("خواندن وضعیت");
    await expect(page.locator("#expiryList li")).toHaveCount(1);
    await page.clock.fastForward(3100);
    await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
    await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
    await expect(page.locator("#expiryWarning")).toBeHidden();
    assert.equal(state.postCalls, 0);
  } finally {
    release(preview(10, 10, 100));
  }
});

test("a stalled start response times out and checks the accepted job without repeating the POST", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await state.open();
  await expect(page.locator("#expiryBadge")).toHaveText("۱۰ روز");
  await page.locator("#btnExpiries").click();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.postHandler = () => pending;
  try {
    await page.locator("#btnExpiryRefresh").click();
    await expect.poll(() => state.postCalls).toBe(1);
    state.payload = preview(1, 50, 100);
    await page.clock.fastForward(15000);
    await expect(page.locator("#expiryWarning")).toContainText("شروع به‌روزرسانی");
    await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
    await page.clock.fastForward(3100);
    await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
    await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
    await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "50");
    assert.equal(state.postCalls, 1);
  } finally {
    release({ status: "started" });
  }
});

test("analytical cards show prices, intrinsic value, score and volume in the mobile layout", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await state.open();
  await page.locator("#btnExpiries").click();
  const metrics = page.locator("#expiryList");
  for (const text of ["بهترین عرضه", "قیمت سهم پایه", "ارزش ذاتی", "اختلاف با ارزش ذاتی (%)", "۲۰٪", "حجم معاملات", "ITM"]) {
    await expect(metrics).toContainText(text);
  }
  await expect(page.locator("#expiryPricingNote")).toContainText("پیش از کارمزد");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => document.documentElement.dataset.theme = value, theme);
    assert(await page.locator("#expirySection").evaluate((element) => element.scrollWidth <= element.clientWidth));
  }
  assert.equal(state.postCalls, 0);
});

test("numeric filters debounce for 300 ms and normalize Persian and Arabic digits", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await page.clock.pauseAt(new Date("2026-10-21T12:00:01Z"));
  await state.open();
  await page.locator("#btnExpiries").click();
  const queries = [];
  state.getHandler = (call, params) => { queries.push(Object.fromEntries(params)); return analyticalSnapshot(); };
  const calls = state.getCalls;
  await page.locator('[data-expiry-filter="min_score"]').fill("۱۹٫۵");
  await page.locator('[data-expiry-filter="min_volume"]').fill("٢٠");
  await page.clock.runFor(299);
  assert.equal(state.getCalls, calls);
  await page.clock.runFor(1);
  await expect.poll(() => state.getCalls).toBe(calls + 1);
  assert.deepEqual(queries[0], { min_score: "19.5", min_volume: "20" });
  await page.locator('[data-expiry-filter="option_type"]').selectOption("call");
  await page.locator('[data-expiry-filter="moneyness"]').selectOption("ITM");
  await page.locator("#expiryUnderlyingFilter").selectOption("2001");
  await expect.poll(() => queries.at(-1)).toMatchObject({ option_type: "call", moneyness: "ITM", underlying_ins_code: "2001" });
  await page.locator("#btnExpiryClearFilters").click();
  await expect.poll(() => queries.at(-1)).toEqual({});
  await expect(page.locator("#btnExpiryClearFilters")).toBeDisabled();
  assert.equal(state.postCalls, 0);
});

test("a slower filter response cannot replace a newer selection", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await state.open();
  await page.locator("#btnExpiries").click();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(analyticalSnapshot()));
  state.getHandler = (call, params) => params.get("option_type") === "call" ? pending
    : analyticalSnapshot({ items: [{ ...analyticalSnapshot().items[0], symbol: "نتیجه فروش", option_type: "put" }] });
  const calls = state.getCalls;
  await page.locator('[data-expiry-filter="option_type"]').selectOption("call");
  await expect.poll(() => state.getCalls).toBe(calls + 1);
  await page.locator('[data-expiry-filter="option_type"]').selectOption("put");
  await expect(page.locator("#expiryList")).toContainText("نتیجه فروش");
  const oldResponse = page.waitForResponse((response) => response.url().includes("option_type=call"));
  release(analyticalSnapshot());
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("#expiryList")).toContainText("نتیجه فروش");
  await expect(page.locator('[data-expiry-filter="option_type"]')).toHaveValue("put");
});

test("changing a numeric filter invalidates pending responses before its debounce finishes", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await page.clock.pauseAt(new Date("2026-10-21T12:00:01Z"));
  await state.open();
  await page.locator("#btnExpiries").click();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(analyticalSnapshot()));
  state.getHandler = (call, params) => params.has("min_score") ? analyticalSnapshot() : pending;
  const calls = state.getCalls;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.getCalls).toBe(calls + 1);
  await page.locator('[data-expiry-filter="min_score"]').fill("10");
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/expiries"));
  release(analyticalSnapshot({ items: [{ ...analyticalSnapshot().items[0], symbol: "پاسخ قدیمی" }] }));
  await oldResponse;
  // Flush promises without advancing the paused 300 ms debounce.
  await page.evaluate(() => Promise.resolve());
  await expect(page.locator("#expiryList")).not.toContainText("پاسخ قدیمی");
  await page.clock.runFor(300);
  await expect.poll(() => state.getCalls).toBe(calls + 2);
  await expect(page.locator("#expiryList")).toContainText("ضخود۸۰۱");
});

test("filters survive progressive refresh and page restoration while the global badge remains independent", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await state.open();
  await page.locator("#btnExpiries").click();
  const queries = [];
  state.getHandler = (call, params) => { queries.push(params.toString()); return state.payload; };
  await page.locator("#expiryUnderlyingFilter").selectOption("2002");
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  state.payload = analyticalSnapshot({
    items: [], total: 0, partial: true, fetched_at: null,
    refresh: { running: true, stage: "metadata", processed: 25, total: 100 },
  });
  await page.clock.fastForward(3100);
  await expect(page.locator("#expiryList li")).toHaveCount(0);
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "25");
  await page.locator("#btnExpiries").click();
  await page.locator("#btnExpiries").click();
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  state.payload = analyticalSnapshot({ items: [], total: 0 });
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryUnderlyingFilter")).toHaveValue("2002");
  await expect(page.locator("#expiryStatus")).toHaveText("نتیجه‌ای مطابق فیلترها نیست");
  assert(queries.every((query) => query === "underlying_ins_code=2002"));
  assert.equal(state.postCalls, 1);
  await page.reload();
  await expect(page.locator("#expiryUnderlyingFilter")).toHaveValue("");
});

test("invalid ranges keep results visible and do not stop refresh status polling", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await page.clock.pauseAt(new Date("2026-10-21T12:00:01Z"));
  await state.open();
  await page.locator("#btnExpiries").click();
  state.postHandler = () => {
    state.payload = analyticalSnapshot({
      partial: true, refresh: { running: true, stage: "metadata", processed: 0, total: 100 },
    });
    return { status: "started" };
  };
  await page.locator("#btnExpiryRefresh").click();
  // Wait for the first status response before editing; otherwise the POST's
  // own follow-up read may legitimately apply the intermediate valid draft.
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "0");
  const queries = [];
  state.getHandler = (call, params) => { queries.push(params.toString()); return state.payload; };
  await page.locator('[data-expiry-filter="min_days"]').fill("5");
  await page.locator('[data-expiry-filter="max_days"]').fill("2");
  await expect(page.locator("#expiryFilterError")).toContainText("حداکثر");
  await expect(page.locator("#expiryList li")).toHaveCount(1);
  state.payload = analyticalSnapshot();
  await page.clock.fastForward(3100);
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryFilterError")).toContainText("حداکثر");
  assert(queries.every((query) => query === ""));
  await page.locator("#btnExpiryClearFilters").click();
  await expect(page.locator("#expiryFilterError")).toBeHidden();
});

test("stale prices and empty score filters explicitly request today's update", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot({ pricing_current: false, market_received_at: "2026-10-20T09:00:00Z" }) });
  await state.open();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryList")).toContainText("نیازمند به‌روزرسانی");
  await expect(page.locator("#expiryPricingNote")).toContainText("امروز");
  state.payload = analyticalSnapshot({ items: [], total: 0, pricing_current: false });
  await page.locator('[data-expiry-filter="min_score"]').fill("0");
  await page.clock.fastForward(310);
  await expect(page.locator("#expiryStatus")).toContainText("فیلتر امتیاز نیازمند");
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  state.payload = analyticalSnapshot({ pricing_current: true });
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator("#expiryList")).toContainText("۲۰٪");
  assert.equal(state.postCalls, 0);
});

test("Tehran midnight disables yesterday's score without fetching the market", async (t) => {
  const data = analyticalSnapshot();
  data.items[0] = { ...data.items[0], end_date: 20261022, days_remaining: 1 };
  data.nearest_days = 1;
  const { page, state } = await fixture(t, { now: "2026-10-21T20:29:00Z", initial: data });
  await state.open();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryList")).toContainText("۲۰٪");
  state.payload = {
    ...data, as_of_date: "2026-10-22", nearest_days: 0, pricing_current: false,
    items: [{ ...data.items[0], days_remaining: 0, intrinsic_gap_percent: null }],
  };
  await page.clock.fastForward(61000);
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  await expect(page.locator("#expiryList")).toContainText("نیازمند به‌روزرسانی");
  assert.equal(state.postCalls, 0);
});

test("an invalid filter cannot cancel reconciliation after a lost refresh response", async (t) => {
  const { page, state } = await fixture(t, { initial: analyticalSnapshot() });
  await page.clock.pauseAt(new Date("2026-10-21T12:00:01Z"));
  await state.open();
  await page.locator("#btnExpiries").click();
  // The server started refreshing, but the POST response did not arrive.
  state.payload = analyticalSnapshot({
    partial: true,
    refresh: { running: true, stage: "metadata", processed: 50, total: 100 },
  });
  state.failPost = true;
  await page.locator("#btnExpiryRefresh").click();
  await expect(page.locator("#expiryWarning")).toContainText("شروع به‌روزرسانی");
  await page.locator('[data-expiry-filter="min_days"]').fill("x");
  await expect(page.locator("#expiryFilterError")).toBeVisible();
  await page.clock.fastForward(3400);
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  await expect(page.locator("#expiryProgress")).toHaveAttribute("value", "50");
  state.payload = analyticalSnapshot({ fetched_at: "2026-10-21T09:05:00Z" });
  await page.clock.fastForward(3100);
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryFilterError")).toBeVisible();
  await expect(page.locator("#expiryWarning")).toBeHidden();
  assert.equal(state.postCalls, 1);
});

test("an invalid filter during the initial read still loads the cached global warning", async (t) => {
  const { page, state } = await fixture(t);
  await page.clock.pauseAt(new Date("2026-10-21T12:00:01Z"));
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(snapshot(10)));
  const queries = [];
  state.getHandler = (call, params) => {
    queries.push(params.toString());
    return call === 1 ? pending : analyticalSnapshot();
  };
  await state.open();
  await page.locator("#btnExpiries").click();
  await page.locator('[data-expiry-filter="max_days"]').fill("x");
  await page.clock.fastForward(310);
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  await expect(page.locator("#expiryFilterError")).toBeVisible();
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/expiries"));
  release(snapshot(10));
  await oldResponse;
  await page.clock.runFor(16);
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  assert(queries.every((query) => query === ""));
  assert.equal(state.postCalls, 0);
});

for (const mode of ["failed", "pending"]) {
  test(`Tehran midnight invalidates cached scores and day counts while a read is ${mode}`, async (t) => {
    const data = analyticalSnapshot();
    data.items[0] = { ...data.items[0], end_date: 20261022, days_remaining: 1 };
    data.nearest_days = 1;
    const { page, state } = await fixture(t, { now: "2026-10-21T20:29:00Z", initial: data });
    await page.clock.pauseAt(new Date("2026-10-21T20:29:01Z"));
    await state.open();
    await page.locator("#btnExpiries").click();
    await expect(page.locator("#expiryList")).toContainText("۲۰٪");
    await expect(page.locator("#expiryBadge")).toHaveText("۱ روز");
    const nextDay = {
      ...data, as_of_date: "2026-10-22", nearest_days: 0, pricing_current: false,
      items: [{ ...data.items[0], days_remaining: 0, intrinsic_gap_percent: null }],
    };
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    t.after(() => release(nextDay));
    state.failReads = mode === "failed";
    state.getHandler = () => pending;
    const calls = state.getCalls;
    await page.clock.fastForward(61000);
    await expect.poll(() => state.getCalls).toBeGreaterThan(calls);
    await expect(page.locator("#expiryList")).toContainText("نیازمند به‌روزرسانی");
    await expect(page.locator("#expiryList")).not.toContainText("۲۰٪");
    await expect(page.locator("#expiryList li")).toHaveCount(1);
    await expect(page.locator("#expiryBadge")).toHaveText("نیازمند بررسی");
    await expect(page.locator("#expiryList .expiry-badge")).toHaveText("نیازمند بررسی");
    await expect(page.locator("#expiryWarning")).toContainText("روز تقویمی تغییر کرده");
    state.failReads = false;
    state.getHandler = () => nextDay;
    if (mode === "pending") release(nextDay);
    else await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator("#expiryBadge")).toHaveText("امروز");
    await expect(page.locator("#expiryList .expiry-badge")).toHaveText("امروز");
    await expect(page.locator("#expiryWarning")).toBeHidden();
    assert.equal(state.postCalls, 0);
  });
}

test("restoring after Tehran midnight invalidates the old view before a pending read returns", async (t) => {
  const data = analyticalSnapshot();
  data.items[0] = { ...data.items[0], end_date: 20261022, days_remaining: 1 };
  data.nearest_days = 1;
  const { page, state } = await fixture(t, { now: "2026-10-21T20:29:00Z", initial: data });
  await page.clock.pauseAt(new Date("2026-10-21T20:29:01Z"));
  await state.open();
  await page.locator("#btnExpiries").click();
  await expect(page.locator("#expiryList")).toContainText("۲۰٪");
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.clock.fastForward(61000);
  const nextDay = {
    ...data, as_of_date: "2026-10-22", nearest_days: 0, pricing_current: false,
    items: [{ ...data.items[0], days_remaining: 0, intrinsic_gap_percent: null }],
  };
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(nextDay));
  state.getHandler = () => pending;
  const calls = state.getCalls;
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect.poll(() => state.getCalls).toBeGreaterThan(calls);
  await expect(page.locator("#expiryBadge")).toHaveText("نیازمند بررسی");
  await expect(page.locator("#expiryList")).toContainText("نیازمند به‌روزرسانی");
  await expect(page.locator("#expiryList")).not.toContainText("۲۰٪");
  release(nextDay);
  await expect(page.locator("#expiryBadge")).toHaveText("امروز");
  assert.equal(state.postCalls, 0);
});

test("an old start response cannot clear a newer pending refresh after restoration", async (t) => {
  const { page, state } = await fixture(t, { initial: snapshot(10) });
  await page.clock.pauseAt(new Date("2026-10-21T12:00:01Z"));
  await state.open();
  await page.locator("#btnExpiries").click();
  let releaseFirst, releaseSecond;
  const first = new Promise((resolve) => { releaseFirst = resolve; });
  const second = new Promise((resolve) => { releaseSecond = resolve; });
  t.after(() => { releaseFirst({ status: "started" }); releaseSecond({ status: "cooldown" }); });
  state.postHandler = () => state.postCalls === 1 ? first : second;
  await page.locator("#btnExpiryRefresh").click();
  await expect.poll(() => state.postCalls).toBe(1);
  // The first job finished, but its start response is still in transit.
  state.payload = snapshot(5, { fetched_at: "2026-10-21T09:05:00Z" });
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect(page.locator("#expiryBadge")).toHaveText("۵ روز");
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await page.locator("#btnExpiryRefresh").click();
  await expect.poll(() => state.postCalls).toBe(2);
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/expiries/refresh"));
  releaseFirst({ status: "started" });
  await oldResponse;
  await page.clock.runFor(16);
  await expect(page.locator("#btnExpiryRefresh")).toBeDisabled();
  await expect(page.locator("#expiryBadge")).toHaveText("۵ روز");
  releaseSecond({ status: "cooldown" });
  await expect(page.locator("#btnExpiryRefresh")).toBeEnabled();
  await expect(page.locator("#expiryWarning")).toContainText("یک دقیقه");
  assert.equal(state.postCalls, 2);
});
