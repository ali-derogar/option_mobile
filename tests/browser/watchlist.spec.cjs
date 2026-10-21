/**
 * Offline browser regressions. Test-only dependency: Playwright.
 * Run: node --test tests/browser/watchlist.spec.cjs
 * CHROME_PATH optionally selects an installed Chromium browser.
 */
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

function underlying(symbol = "خودرو") {
  return { underlying_key: "2001", underlying_ins_code: "2001", underlying_symbol: symbol, available: true, data_status: "ready", contract_count: 1 };
}

async function fixture(t, view = "symbols") {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  t.after(() => context.close());
  const page = await context.newPage();
  await page.clock.install();
  const state = {
    items: [{ ins_code: "2001", symbol: "خودرو", name: "خودرو", available: true, options_trade_volume: 10, underlying_trade_volume: 20 }],
    saved: [], refresh: { running: false, last_result: null },
    summary: { underlying_count: 1, contract_count: 1 },
    catalogCalls: 0, refreshCalls: 0, errors: [],
  };
  page.on("pageerror", (error) => state.errors.push(error.message));
  await page.route("http://options.test/**", async (route) => {
    const url = new URL(route.request().url());
    const json = (body) => route.fulfill({ json: body });
    switch (url.pathname) {
      case "/api/activation/status": return json({ activated: true });
      case "/api/watchlist":
        if (route.request().method() === "PUT") state.saved = route.request().postDataJSON().ins_codes;
        return json({ ins_codes: state.saved });
      case "/api/underlyings/catalog": {
        state.catalogCalls += 1;
        const items = state.catalogHandler ? await state.catalogHandler(state.catalogCalls) : state.items;
        return json({ items, refresh: { running: false } });
      }
      case "/api/underlyings/catalog/refresh": return json({ status: "started" });
      case "/api/refresh/status":
        state.refreshCalls += 1;
        if (state.failRefreshStatus) return route.fulfill({ status: 503, json: { detail: "status unavailable" } });
        return json(state.refreshHandler ? await state.refreshHandler(state.refreshCalls) : state.refresh);
      case "/api/dates": return json({ items: [], latest: null });
      case "/api/calendar/today": return json({ gregorian_date: "2026-10-21" });
      case "/api/summary": return json(state.summary);
      case "/api/underlyings": {
        if (state.failSearch && url.searchParams.get("q")) return route.fulfill({ status: 503, json: { detail: "search unavailable" } });
        const items = state.underlyingsHandler ? await state.underlyingsHandler(url.searchParams.get("q")) : [underlying()];
        if (items.error) return route.fulfill({ status: 503, json: { detail: items.error } });
        return json({ items, total: items.length, watchlist_count: 1 });
      }
    }
    const file = url.pathname.startsWith("/static/") ? url.pathname.slice(1)
      : url.pathname.endsWith("/symbols") ? "symbols.html" : "index.html";
    const mime = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".ttf": "font/ttf" };
    return route.fulfill({ body: await readFile(path.join(webRoot, file)), contentType: mime[path.extname(file)] });
  });
  state.open = async () => {
    await page.goto(`http://options.test/dashboard/test${view === "symbols" ? "/symbols" : ""}`);
    if (view === "symbols") await expect(page.locator("#fetchCatalog")).toBeEnabled();
    else await expect(page.locator("#loadingOverlay")).toHaveClass(/hidden/);
  };
  t.after(() => assert.deepEqual(state.errors, []));
  return { page, state };
}

test("a draft selection can be removed after the instrument becomes unavailable", async (t) => {
  const { page, state } = await fixture(t);
  await state.open();
  await page.locator("#symbolList input").check();
  state.items = [{ ...state.items[0], available: false }];
  await page.clock.fastForward(3100);
  await expect(page.getByText("در فهرست فعلی بازار موجود نیست", { exact: true })).toBeVisible();
  await expect(page.locator("#symbolList input")).toBeEnabled();
  await page.locator("#symbolList input").uncheck();
  await expect(page.locator("#symbolList input")).toBeDisabled();
  await expect(page.locator("#saveSymbols")).toBeDisabled();
});

test("older catalogue responses cannot replace a newer catalogue", async (t) => {
  const { page, state } = await fixture(t);
  const original = state.items.map((row) => ({ ...row }));
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.catalogHandler = async (call) => call === 2 ? pending : state.items;
  await state.open();
  await page.clock.fastForward(3100);
  await expect.poll(() => state.catalogCalls).toBe(2);
  state.items = [{ ...original[0], name: "نام تازه" }];
  await page.locator("#fetchCatalog").click();
  await expect(page.getByText("نام تازه", { exact: true })).toBeVisible();
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/underlyings/catalog"));
  release(original);
  await oldResponse;
  await page.waitForTimeout(100);
  assert.equal(await page.locator(".symbol-identity small").textContent(), "نام تازه");
});

test("restoring the editor refreshes its status and ignores responses from its previous visit", async (t) => {
  const { page, state } = await fixture(t);
  const original = state.items.map((row) => ({ ...row }));
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(original));
  state.catalogHandler = (call) => call === 2 ? pending : state.items;
  await state.open();
  await page.clock.pauseAt(await page.evaluate(() => Date.now()));
  await page.locator("#symbolList input").check();
  await expect(page.locator("#saveSymbols")).toBeEnabled();
  await page.clock.fastForward(3100);
  await expect.poll(() => state.catalogCalls).toBe(2);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  state.items = [{ ...original[0], name: "نام تازه" }];
  state.refresh = { running: true };
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect(page.locator("#saveSymbols")).toBeDisabled();
  await expect(page.getByText("نام تازه", { exact: true })).toBeVisible();
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/underlyings/catalog"));
  release(original);
  await oldResponse;
  await page.waitForTimeout(100);
  await expect(page.getByText("نام تازه", { exact: true })).toBeVisible();
  await expect(page.locator("#saveSymbols")).toBeDisabled();
  await expect(page.locator("#symbolList input")).toBeChecked();
  state.refresh = { running: false };
  await page.clock.fastForward(3100);
  await expect(page.locator("#saveSymbols")).toBeEnabled();
});

test("dashboard resumes an existing refresh and reloads data on completion", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: true, last_result: null, message: "در حال دریافت" };
  await state.open();
  await expect(page.locator("#btnRefresh")).toBeDisabled();
  assert(state.refreshCalls > 0);
  state.refresh = { running: false, last_result: { options: 2 } };
  state.summary.contract_count = 2;
  await page.clock.fastForward(3100);
  await expect(page.locator("#statBuyOi")).toHaveText("۲");
  await expect(page.locator("#btnRefresh")).toBeEnabled();
});

test("a slower previous search cannot overwrite the latest search results", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let oldStarted = false;
  state.underlyingsHandler = (q) => {
    if (q === "قدیمی") { oldStarted = true; return pending; }
    return [underlying(q || "خودرو")];
  };
  await state.open();
  await page.locator("#searchInput").fill("قدیمی");
  await page.clock.fastForward(300);
  await expect.poll(() => oldStarted).toBe(true);
  await page.locator("#searchInput").fill("تازه");
  await page.clock.fastForward(300);
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("تازه");
  const oldResponse = page.waitForResponse((response) => new URL(response.url()).searchParams.get("q") === "قدیمی");
  release([underlying("قدیمی")]);
  await oldResponse;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("تازه");
});

test("a refresh status failure does not discard successfully loaded dashboard data", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.failRefreshStatus = true;
  await state.open();
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("خودرو");
});

test("a failed search preserves the previous rows and reports the error", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  await state.open();
  state.failSearch = true;
  await page.locator("#searchInput").fill("نماد");
  await page.clock.fastForward(300);
  await expect(page.locator("#toast")).toHaveText("search unavailable");
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("خودرو");
});

test("slow refresh status requests never overlap", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release({ running: false, last_result: { options: 2 } }));
  state.refreshHandler = (call) => call === 1 ? { running: true } : pending;
  await state.open();
  await page.clock.fastForward(3100);
  await expect.poll(() => state.refreshCalls).toBe(2);
  await page.clock.fastForward(3100);
  await page.waitForTimeout(100);
  assert.equal(state.refreshCalls, 2);
  state.summary.contract_count = 2;
  state.refreshHandler = () => ({ running: false, last_result: { options: 2 } });
  release({ running: false, last_result: { options: 2 } });
  await expect(page.locator("#statBuyOi")).toHaveText("۲");
  await expect(page.locator("#btnRefresh")).toBeEnabled();
});

test("a failed previous search does not show an error after a newer search succeeded", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release({ error: "old search failed" }));
  let oldStarted = false;
  state.underlyingsHandler = (query) => {
    if (query === "قدیمی") { oldStarted = true; return pending; }
    return [underlying(query || "خودرو")];
  };
  await state.open();
  await page.locator("#searchInput").fill("قدیمی");
  await page.clock.fastForward(300);
  await expect.poll(() => oldStarted).toBe(true);
  await page.locator("#searchInput").fill("تازه");
  await page.clock.fastForward(300);
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("تازه");
  const oldResponse = page.waitForResponse((response) => new URL(response.url()).searchParams.get("q") === "قدیمی");
  release({ error: "old search failed" });
  await oldResponse;
  await page.waitForTimeout(100);
  assert.equal(await page.locator("#toast").isVisible(), false);
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("تازه");
});

test("three consecutive refresh polling errors stop polling and restore the controls", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: true };
  await state.open();
  state.failRefreshStatus = true;
  for (let call = 2; call <= 4; call += 1) {
    await page.clock.fastForward(3100);
    await expect.poll(() => state.refreshCalls).toBe(call);
    await page.waitForTimeout(20);
  }
  await expect(page.locator("#btnRefresh")).toBeEnabled();
  await expect(page.locator("#toast")).toContainText("ارتباط با وضعیت به‌روزرسانی قطع شد");
  await expect(page.locator("#lastUpdate")).toContainText("دریافت وضعیت به‌روزرسانی متوقف شد");
  await page.clock.fastForward(3100);
  await page.waitForTimeout(100);
  assert.equal(state.refreshCalls, 4);
});

test("refresh polling recovers after a temporary connection failure", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: true };
  await state.open();
  state.failRefreshStatus = true;
  await page.clock.fastForward(3100);
  await expect(page.locator("#lastUpdate")).toContainText("در حال تلاش دوباره");
  state.failRefreshStatus = false;
  state.refresh = { running: false, last_result: { options: 2 } };
  state.summary.contract_count = 2;
  await page.clock.fastForward(3100);
  await expect(page.locator("#statBuyOi")).toHaveText("۲");
  await expect(page.locator("#btnRefresh")).toBeEnabled();
});

test("restoring a cached page ignores refresh responses from its previous visit", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release({ running: true, message: "وضعیت قدیمی" }));
  state.refreshHandler = (call) => call === 1 ? { running: true } : pending;
  await state.open();
  await page.clock.fastForward(3100);
  await expect.poll(() => state.refreshCalls).toBe(2);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  state.refreshHandler = () => ({ running: false, last_result: { options: 2 } });
  state.summary.contract_count = 2;
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await expect(page.locator("#statBuyOi")).toHaveText("۲");
  await expect.poll(() => state.refreshCalls).toBe(3);
  const oldResponse = page.waitForResponse((response) => response.url().endsWith("/api/refresh/status"));
  release({ running: true, message: "وضعیت قدیمی" });
  await oldResponse;
  await page.waitForTimeout(100);
  assert.equal((await page.locator("#lastUpdate").innerText()).includes("وضعیت قدیمی"), false);
  await expect(page.locator("#btnRefresh")).toBeEnabled();
  await page.clock.fastForward(3100);
  await page.waitForTimeout(100);
  assert.equal(state.refreshCalls, 3);
});

test("a zero-data refresh does not report success while showing previous rows", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: true };
  await state.open();
  state.refresh = { running: false, last_result: { options: 0, client_type_stats: 0 } };
  await page.clock.fastForward(3100);
  await expect(page.locator("#refreshWarning")).toContainText("داده تازه‌ای دریافت نشد");
  await expect(page.locator("#toast")).not.toContainText("انجام شد");
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("خودرو");
});

test("partially received client type data remains visibly marked after reloading", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: false, last_result: { options: 2, client_type_stats: 1 } };
  await state.open();
  await expect(page.locator("#refreshWarning")).toContainText("حقیقی/حقوقی");
  await expect(page.locator("#refreshWarning")).toContainText("۱ از ۲");
  await page.reload();
  await expect(page.locator("#refreshWarning")).toBeVisible();
});

test("a complete refresh has no incomplete-data warning", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: false, last_result: { options: 2, client_type_stats: 2 } };
  await state.open();
  await expect(page.locator("#refreshWarning")).toBeHidden();
});

test("a failed refresh remains visible when returning to the dashboard", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.refresh = { running: false, last_result: null, last_error: "خطا در دریافت اطلاعات بازار" };
  await state.open();
  await expect(page.locator("#refreshWarning")).toHaveText("خطا در دریافت اطلاعات بازار");
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("خودرو");
});

test("Basama can be found and saved even when both trading volumes are zero", async (t) => {
  const { page, state } = await fixture(t);
  const code = "41625340598198551";
  state.items.push({
    ins_code: code, symbol: "بساما", name: "بیمه سامان", available: true,
    options_trade_volume: 0, underlying_trade_volume: 0,
  });
  state.underlyingsHandler = () => [{
    ...underlying("بساما"), underlying_key: code, underlying_ins_code: code,
    data_status: "needs_refresh", contract_count: null,
  }];
  await state.open();
  await page.locator("#symbolSearch").fill("بساما");
  await expect(page.locator("#symbolList li")).toHaveCount(1);
  await expect(page.locator("#symbolList .symbol-volume strong")).toHaveText(["۰", "۰"]);
  await page.getByRole("checkbox", { name: "انتخاب بساما", exact: true }).check();
  await page.clock.fastForward(3100);
  await expect(page.getByRole("checkbox", { name: "انتخاب بساما", exact: true })).toBeChecked();
  await page.locator("#saveSymbols").click();
  await page.waitForURL("http://options.test/dashboard/test");
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("بساما");
  await expect(page.locator("#mobileCardList")).toContainText("نیازمند به‌روزرسانی");
  assert.deepEqual(state.saved, [code]);
});
