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

async function fixture(t, view = "symbols", { pauseClock = false, timezoneId = "UTC" } = {}) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId });
  t.after(() => context.close());
  const page = await context.newPage();
  if (pauseClock) {
    const start = Date.UTC(2026, 9, 21, 12);
    await page.clock.install({ time: new Date(start) });
    // Freeze before navigation, while no application timers have started.
    await page.clock.pauseAt(new Date(start + 60000));
  } else {
    await page.clock.install();
  }
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
        return json({ items, fetched_at: state.catalogFetchedAt || null, refresh: { running: false } });
      }
      case "/api/underlyings/catalog/refresh": return json({ status: "started" });
      case "/api/refresh/status":
        state.refreshCalls += 1;
        if (state.failRefreshStatus) return route.fulfill({ status: 503, json: { detail: "status unavailable" } });
        return json(state.refreshHandler ? await state.refreshHandler(state.refreshCalls) : state.refresh);
      case "/api/dates": return json({ items: [], latest: null });
      case "/api/expiries": return json({ items: [], total: 0, as_of_date: "2026-10-21", fetched_at: null, nearest_days: null, refresh: { running: false } });
      case "/api/calendar/today": return json({ gregorian_date: "2026-10-21" });
      case "/api/calendar/1405/7/events": return json(state.calendarData || {
        year: 1405, month: 7, days: [], events: [], holidays: [], events_available: false, source: "fallback",
      });
      case "/api/summary": return json(state.summaryHandler ? await state.summaryHandler(url.searchParams) : state.summary);
      case "/api/underlyings": {
        if (state.failSearch && url.searchParams.get("q")) return route.fulfill({ status: 503, json: { detail: "search unavailable" } });
        const items = state.underlyingsHandler ? await state.underlyingsHandler(url.searchParams.get("q"), url.searchParams) : [underlying()];
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
  const { page, state } = await fixture(t, "symbols", { pauseClock: true });
  const original = state.items.map((row) => ({ ...row }));
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  t.after(() => release(original));
  state.catalogHandler = (call) => call === 2 ? pending : state.items;
  await state.open();
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

test("the date picker remains usable and marks unavailable holiday information", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  await state.open();
  await page.locator("#datePickerButton").click();
  await expect(page.locator("#datePickerFoot")).toContainText("اطلاعات تعطیلات در دسترس نیست");
  await expect(page.locator("#datePickerTitle")).toContainText("۱۴۰۵");
  await expect(page.locator("#datePickerTitle")).not.toContainText("٬");
  await expect(page.locator(".date-picker-day")).toHaveCount(30);
  await expect(page.locator('.date-picker-day[data-date="2026-10-21"]')).toBeEnabled();
  await page.locator('.date-picker-day[data-date="2026-10-21"]').click();
  await expect(page).toHaveURL(/date=2026-10-21/);
  await expect(page.locator("#datePickerButton")).toContainText("۱۴۰۵");
  await expect(page.locator("#datePickerButton")).not.toContainText("٬");
});

test("holiday information recovers after a temporary calendar outage without reloading the page", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  await state.open();
  await page.locator("#datePickerButton").click();
  await expect(page.locator("#datePickerFoot")).toContainText("اطلاعات تعطیلات در دسترس نیست");
  await page.locator("#datePickerButton").click();
  state.calendarData = {
    year: 1405, month: 7, days: [{ day: 29, is_holiday: true }],
    events: [], holidays: [], events_available: true, source: "time.ir",
  };
  await page.clock.fastForward(61000);
  await page.locator("#datePickerButton").click();
  await expect(page.locator("#datePickerFoot")).toHaveText("روزهای تعطیل با رنگ قرمز مشخص شده‌اند");
  await expect(page.locator('.date-picker-day[data-date="2026-10-21"]')).toHaveClass(/is-holiday/);
});

test("a failed date change restores both the previous rows and the previous summary", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.summaryHandler = (params) => ({ ...state.summary, contract_count: params.get("date") ? 17 : 1 });
  state.underlyingsHandler = (query, params) => params.get("date") ? { error: "date unavailable" } : [underlying()];
  await state.open();
  await expect(page.locator("#statBuyOi")).toHaveText("۱");
  await page.locator("#datePickerButton").click();
  await page.locator('.date-picker-day[data-date="2026-10-20"]').click();
  await expect(page.locator("#toast")).toHaveText("خطا در تغییر تاریخ");
  await expect(page.locator("#mobileCardList .mobile-card-head strong")).toHaveText("خودرو");
  await expect(page).toHaveURL("http://options.test/dashboard/test");
  await expect(page.locator("#statBuyOi")).toHaveText("۱");
  await expect(page.locator("#lastUpdate")).not.toHaveClass(/stale/);
});

test("calendar events from another month cannot mark the current month as a holiday", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  state.calendarData = {
    year: 1405, month: 7, days: [], events_available: true,
    events: [{ id: 1, title: "رویداد ماه دیگر", jalali_date: "1405-08-29", is_holiday: true }],
  };
  await state.open();
  await page.locator("#datePickerButton").click();
  const day = page.locator('.date-picker-day[data-date="2026-10-21"]');
  await expect(day).not.toHaveClass(/is-holiday/);
  await expect(day).not.toHaveAttribute("title", /رویداد ماه دیگر/);
});

test("catalogue dates accept compact market dates and always show Tehran capture time", async (t) => {
  const { page, state } = await fixture(t, "symbols", {timezoneId: "America/New_York"});
  state.items[0].market_date = "20261021";
  state.catalogFetchedAt = "2026-10-21T08:00:00Z";
  await state.open();
  await expect(page.locator(".symbol-volume small").filter({hasText: "تاریخ معامله"})).toContainText("۱۴۰۵");
  await expect(page.locator(".symbol-volume small").filter({hasText: "تاریخ معامله"})).not.toContainText("20261021");
  const expected = await page.evaluate(() => new Date("2026-10-21T08:00:00Z").toLocaleString("fa-IR", {timeZone: "Asia/Tehran"}));
  await expect(page.locator("#catalogStatus")).toContainText(expected);
});

test("unknown trading volume is not labelled as no trades", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  await state.open();
  const result = await page.evaluate(() => ({
    unknownVolume: activityLabel({contract_count: 2, trade_volume: null}),
    unknownCount: activityLabel({contract_count: null, trade_volume: 0}),
    zero: activityLabel({contract_count: 2, trade_volume: 0}),
  }));
  assert.match(result.unknownVolume, /نامشخص/);
  assert.match(result.unknownCount, /نامشخص/);
  assert.equal(result.zero, "بدون معامله");
});


test("partial aggregate coverage is visible and consistent in summary and cards", async (t) => {
  const { page, state } = await fixture(t, "dashboard");
  const coverage = {known: 1, total: 2, complete: false};
  state.summary = {...state.summary, contract_count: 2, total_trade_volume: 50, coverage: {total_trade_volume: coverage}};
  state.underlyingsHandler = () => [{...underlying(), contract_count: 2, trade_volume: 50, coverage: {trade_volume: coverage}}];
  await state.open();
  await expect(page.locator("#statLegalFlow")).toHaveText("—");
  await expect(page.locator("#statVolumeCoverage")).toContainText("داده ناقص");
  await page.locator(".mobile-underlying-card").click();
  await expect(page.locator(".mobile-underlying-card .metric-volume strong")).toHaveText("—");
  await expect(page.locator(".mobile-underlying-card .mobile-card-note")).toContainText("نامشخص");
});
