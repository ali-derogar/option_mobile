/** Offline regressions using the real frontend and Chart.js, with deterministic API responses. */
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { readFile } = require("node:fs/promises");
const path = require("node:path");
const { chromium, expect } = require("playwright/test");
const webRoot = path.resolve(__dirname, "../../src/options/web");
let browser;
before(async () => { browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) }); });
after(async () => { await browser?.close(); });

const metric = (value, known = 1, total = 1) => ({ value, known, total, complete: known === total });
function summary(call, put) {
  return { call: metric(call), put: metric(put), total: metric(call + put), call_put_ratio: put ? call / put : null, ratio_state: put ? "finite" : call ? "infinite" : "unavailable" };
}
function payload(params, changes = {}) {
  const key = params.get("underlying_key") || "2001";
  const expiries = key === "2001" ? ["20261209", "20270113"] : ["20270113"];
  const expiry = expiries.includes(params.get("expiry")) ? params.get("expiry") : "all";
  const selected = expiry === "all" ? 100_000_000 : 40_000_000;
  const historical = Boolean(params.get("date"));
  const strikes = [
    { strike_price: 1000, call_oi: historical ? null : 100, put_oi: historical ? null : 40, call_change: historical ? null : 30, put_change: historical ? null : -15 },
    { strike_price: 1200, call_oi: historical ? null : 20, put_oi: historical ? null : 10, call_change: historical ? null : -10, put_change: historical ? null : 5 },
  ];
  strikes.forEach((row) => { row.coverage = Object.fromEntries(["call_oi", "put_oi", "call_change", "put_change"].map((k) => [k, metric(row[k], historical ? 0 : 1)])); });
  return {
    underlying_key: key, expiry, expiries, underlyings: [{ key: "2001", symbol: "خودرو" }, { key: "2002", symbol: "بساما" }],
    dates: ["2026-10-20", "2026-10-21"], data_date: params.get("date") || "2026-10-21", received_at: "2026-10-22T08:00:00+00:00",
    source: historical ? "public_history" : "public_cdn", spot_price: 1100, spot_price_kind: "last", atm_strike: 1000,
    strikes, peaks: { call: { strike_price: 1000, ...metric(100) }, put: { strike_price: 1000, ...metric(40) } },
    summaries: { market: summary(20_000_000_000, 10_000_000_000), underlying: summary(100_000_000, 50_000_000), selection: summary(selected, selected / 2) },
    quality: { oi: metric(historical ? null : 160, historical ? 0 : 4, 4), change: metric(historical ? null : 10, historical ? 0 : 4, 4), unknown_type_count: 0, invalid_strike_count: 0 },
    cached: true, warning: historical ? "موقعیت باز تاریخی موجود نیست." : null, refresh: { running: false, last_error: null }, ...changes,
  };
}

async function fixture(t, width = 390) {
  const context = await browser.newContext({ viewport: { width, height: 844 }, hasTouch: true, timezoneId: "America/New_York" });
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  await page.clock.install({ time: new Date("2026-10-22T10:00:00Z") });
  const state = { errors: [], requests: [], posts: [], clientTypeRequests: [], refreshStatus: "cached" };
  page.on("pageerror", (error) => state.errors.push(error.message));
  await page.route("http://options.test/**", async (route) => {
    const url = new URL(route.request().url());
    const json = (data) => route.fulfill({ json: data });
    if (url.pathname === "/api/client-type" || url.pathname.startsWith("/api/client-type/")) {
      const codes = route.request().method() === "POST" ? route.request().postDataJSON().ins_codes : [url.pathname.split("/").at(-1)];
      state.clientTypeRequests.push(codes);
      return json(state.clientTypeHandler ? await state.clientTypeHandler(codes) : { items: [] });
    }
    if (url.pathname === "/api/option-sentiment") {
      state.requests.push(new URLSearchParams(url.searchParams));
      if (state.failRead) return route.fulfill({ status: 503, json: { detail: "خطا در دریافت تحلیل" } });
      return json(state.handler ? await state.handler(url.searchParams) : payload(url.searchParams));
    }
    if (url.pathname === "/api/option-sentiment/refresh") {
      state.posts.push(route.request().postDataJSON());
      if (state.failPost) return route.fulfill({ status: 503, json: { detail: "خطا در تازه‌سازی" } });
      return json(state.postHandler ? await state.postHandler() : { status: state.refreshStatus });
    }
    if (state.failDashboardRead && url.pathname === "/api/summary") {
      return route.fulfill({ status: 503, json: { detail: "خطا در دریافت داشبورد" } });
    }
    switch (url.pathname) {
      case "/api/activation/status": return json({ activated: true });
      case "/api/dates": return json({ items: ["2026-10-21"], latest: "2026-10-21" });
      case "/api/calendar/today": return json({ gregorian_date: "2026-10-22" });
      case "/api/calendar/1405/7/events": return json({ year: 1405, month: 7, days: [], events: [], holidays: [], events_available: false });
      case "/api/summary": return json({ underlying_count: 1, contract_count: 1 });
      case "/api/refresh/status": return json({ running: false });
      case "/api/expiries": return json({ items: [], total: 0, refresh: { running: false } });
      case "/api/client-type": return json({ items: [] });
      case "/api/underlyings/2001/contracts": return json({ underlying: { underlying_key: "2001", underlying_symbol: "خودرو" }, items: [
        { ins_code: "1", row_key: "1", symbol: "ضخود", underlying_key: "2001", option_type: "call", strike_price: 1000, end_date: 20261209, moneyness: "ITM", trade_value: 10, buy_open_positions: 1 },
      ] });
    }
    if (url.pathname.startsWith("/api/")) return json({ items: [] });
    const file = url.pathname.startsWith("/static/") ? url.pathname.slice(1) : "index.html";
    const mime = { ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".ttf": "font/ttf" };
    return route.fulfill({ body: await readFile(path.join(webRoot, file)), contentType: mime[path.extname(file)] });
  });
  await page.goto("http://options.test/dashboard/test/underlying/2001");
  await expect(page.locator("#loadingOverlay")).toHaveClass(/hidden/);
  await page.locator("#contextAnalysisButton").click();
  const open = async () => {
    await page.getByRole("tab", { name: "سنتیمنت آپشن", exact: true }).click();
    await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  };
  t.after(() => assert.deepEqual(state.errors, []));
  return { page, state, open };
}

test("isolated filters, distinct summary scopes, numeric price positions and signed bars", async (t) => {
  const { page, state, open } = await fixture(t);
  await page.evaluate(() => { state.filters.type = "call"; state.filters.strike = "1000"; state.filters.expiry = "20261209"; });
  await open();
  assert.equal(state.requests[0].get("expiry"), "all");
  assert.equal(state.requests[0].get("date"), null);
  assert.equal(state.posts.length, 1);
  assert.deepEqual(state.posts[0], { date: null });
  await expect(page.locator("[data-sentiment-summary]")).toHaveCount(2);
  await expect(page.locator('[data-sentiment-summary="market"]')).toContainText("میلیارد تومان");
  await expect(page.locator('[data-sentiment-summary="underlying"]')).toContainText("میلیون تومان");
  await expect(page.locator('[data-sentiment-summary="selection"]')).toHaveCount(0);
  const charts = await page.evaluate(() => {
    const oi = Chart.getChart("sentimentOiChart"), change = Chart.getChart("sentimentChangeChart");
    return { scale: oi.scales.x.type, x: [1000, 1100, 1200].map((v) => oi.scales.x.getPixelForValue(v)), ys: change.data.datasets.flatMap((d) => d.data.map((p) => p.y)), count: Object.keys(Chart.instances).length };
  });
  assert.equal(charts.scale, "linear");
  assert.ok(Math.abs((charts.x[0] + charts.x[2]) / 2 - charts.x[1]) < 0.01);
  assert.ok(charts.ys.includes(-15) && charts.ys.includes(30));
  assert.equal(charts.count, 2);
  await expect(page.locator("#sentimentPeaks")).toContainText("۱۰۰");
  await expect(page.locator("#typeFilter")).toBeHidden();
});

test("four position modes support every individual selection and combination", async (t) => {
  const { page, state, open } = await fixture(t, 360);
  await open();
  const modes = [
    { key: "buy_call", label: "خرید Call", values: [100, 20], color: "accent" },
    { key: "buy_put", label: "خرید Put", values: [40, 10], color: "purple" },
    { key: "sell_call", label: "فروش Call", values: [100, 20], color: "amber" },
    { key: "sell_put", label: "فروش Put", values: [40, 10], color: "red" },
  ];
  await expect(page.locator("[data-oi-mode]")).toHaveCount(4);
  await expect(page.locator("[data-oi-filter], #sentimentOiClear")).toHaveCount(0);
  const changeId = await page.evaluate(() => Chart.getChart("sentimentChangeChart").id);
  const summaries = await page.locator("#sentimentSummaries").textContent();
  const requestCount = state.requests.length;
  for (let mask = 0; mask < 16; mask += 1) {
    for (const [index, mode] of modes.entries()) {
      await page.getByRole("checkbox", { name: mode.label, exact: true }).setChecked(Boolean(mask & (1 << index)));
    }
    const selected = modes.filter((_, index) => mask & (1 << index));
    const chart = await page.evaluate(() => {
      const oi = Chart.getChart("sentimentOiChart");
      return {
        series: oi?.data.datasets.map((dataset) => ({ label: dataset.label, values: dataset.data.map((point) => point.y), color: dataset.backgroundColor })) || [],
        palette: chartPalette(), changeId: Chart.getChart("sentimentChangeChart").id,
        charts: Object.keys(Chart.instances).length,
      };
    });
    assert.deepEqual(chart.series, selected.map((mode) => ({ label: mode.label, values: mode.values, color: chart.palette[mode.color] })));
    assert.equal(chart.changeId, changeId);
    assert.equal(chart.charts, selected.length ? 2 : 1);
    assert.equal(await page.locator("#sentimentSummaries").textContent(), summaries);
    await expect(page.locator("#sentimentOiLegend .sentiment-position-legend")).toHaveCount(selected.length);
    if (!selected.length) await expect(page.locator("#sentimentOiEmpty")).toContainText("حداقل یکی از چهار حالت");
  }
  assert.equal(state.requests.length, requestCount);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
});

test("position combinations survive refresh, theme and tab changes", async (t) => {
  const { page, open } = await fixture(t);
  await open();
  await page.getByRole("checkbox", { name: "خرید Put", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Call", exact: true }).uncheck();
  const selection = async () => page.evaluate(() => Chart.getChart("sentimentOiChart").data.datasets.map((dataset) => dataset.label));
  assert.deepEqual(await selection(), ["خرید Call", "فروش Put"]);
  await page.evaluate(() => applyTheme("dark"));
  await page.locator("#sentimentRefresh").click();
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect(page.locator('[data-sentiment-summary="selection"] strong').first()).toContainText("۴ میلیون");
  await page.getByRole("tab", { name: "حقیقی", exact: true }).click();
  await open();
  assert.deepEqual(await selection(), ["خرید Call", "فروش Put"]);
  await expect(page.getByRole("checkbox", { name: "خرید Call", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "فروش Put", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "خرید Put", exact: true })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "فروش Call", exact: true })).not.toBeChecked();
});

test("restoring the page keeps cached charts and position controls usable when dashboard reads fail", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  await page.getByRole("checkbox", { name: "خرید Put", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Call", exact: true }).uncheck();
  const figures = await page.locator("#sentimentSummaries").textContent();
  state.failDashboardRead = true;
  state.failRead = true;
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
  await expect(page.locator("#loadingOverlay")).toHaveClass(/hidden/);
  await expect.poll(() => page.evaluate(() => Object.keys(Chart.instances).length)).toBe(2);
  await expect(page.locator("#sentimentStatus")).toContainText("خطا");
  assert.equal(await page.locator("#sentimentSummaries").textContent(), figures);
  assert.deepEqual(await page.evaluate(() => Chart.getChart("sentimentOiChart").data.datasets.map((dataset) => dataset.label)), ["خرید Call", "فروش Put"]);
  await page.getByRole("checkbox", { name: "خرید Call", exact: true }).uncheck();
  assert.deepEqual(await page.evaluate(() => Chart.getChart("sentimentOiChart").data.datasets.map((dataset) => dataset.label)), ["فروش Put"]);
  await expect(page.locator("#sentimentRefresh")).toBeEnabled();
});

test("restoring the page ignores a pending response from the previous visit and preserves selected modes", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  await page.getByRole("checkbox", { name: "خرید Put", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Call", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Put", exact: true }).uncheck();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.handler = (params) => params.get("expiry") === "20261209" ? pending : payload(params);
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect.poll(() => state.requests.at(-1).get("expiry")).toBe("20261209");
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
  await expect(page.locator("#loadingOverlay")).toHaveClass(/hidden/);
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  release(payload(new URLSearchParams({ expiry: "20261209" })));
  await page.waitForTimeout(100);
  await expect(page.locator("#sentimentExpiry")).toHaveValue("all");
  await expect(page.locator('[data-sentiment-summary="underlying"] strong').first()).toContainText("۱۰ میلیون");
  await expect(page.locator('[data-sentiment-summary="selection"]')).toHaveCount(0);
  assert.deepEqual(await page.evaluate(() => Chart.getChart("sentimentOiChart").data.datasets.map((dataset) => dataset.label)), ["خرید Call"]);
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 2);
  assert.equal(state.posts.length, 2);
});

test("restoring a different analysis tab does not restart the hidden sentiment module", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  await page.getByRole("tab", { name: "حقیقی", exact: true }).click();
  const reads = state.requests.length;
  const posts = state.posts.length;
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
  await expect(page.locator("#loadingOverlay")).toHaveClass(/hidden/);
  await page.clock.fastForward(10000);
  assert.equal(state.requests.length, reads);
  assert.equal(state.posts.length, posts);
  assert.equal(await page.evaluate(() => Chart.getChart("sentimentOiChart") == null), true);
});

test("combining both positions of one option does not double coverage or turn missing OI into zero", async (t) => {
  const { page, state, open } = await fixture(t);
  state.handler = (params) => {
    const result = payload(params);
    result.strikes.forEach((row) => {
      row.call_oi = null;
      row.coverage.call_oi = metric(null, 0, 1);
      row.coverage.put_oi = metric(row.put_oi, 1, 2);
    });
    result.peaks.call = null;
    return result;
  };
  await open();
  await page.getByRole("checkbox", { name: "خرید Call", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Call", exact: true }).uncheck();
  await expect(page.locator("#sentimentOiCoverage")).toHaveText("داده ناقص: ۲ از ۴ قرارداد");
  assert.deepEqual(await page.evaluate(() => Chart.getChart("sentimentOiChart").data.datasets.map((dataset) => dataset.data.map((point) => point.y))), [[40, 10], [40, 10]]);
  const tooltip = await page.evaluate(() => {
    const chart = Chart.getChart("sentimentOiChart");
    return chart.options.plugins.tooltip.callbacks.label({ datasetIndex: 1, dataIndex: 0, dataset: chart.data.datasets[1], parsed: { x: 1000, y: 40 } });
  });
  assert.match(tooltip, /فروش Put: ۴۰ قرارداد · داده ناقص: ۱ از ۲ قرارداد/);
  await page.getByRole("checkbox", { name: "خرید Put", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Put", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "فروش Call", exact: true }).check();
  await expect(page.locator("#sentimentOiCoverage")).toHaveText("داده ناقص: ۰ از ۲ قرارداد");
  await expect(page.locator("#sentimentOiEmpty")).toContainText("موجود نیست");
  assert.equal(await page.evaluate(() => Chart.getChart("sentimentOiChart") == null), true);
});

test("expiry, asset and historical filters update all sections without closing analysis", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect(page.locator("[data-sentiment-summary]")).toHaveCount(3);
  await expect(page.locator('[data-sentiment-summary="selection"] strong').first()).toContainText("۴ میلیون");
  await expect(page.locator('[data-sentiment-summary="underlying"] strong').first()).toContainText("۱۰ میلیون");
  await page.locator("#sentimentExpiry").selectOption("all");
  await expect(page.locator("[data-sentiment-summary]")).toHaveCount(2);
  await expect(page.locator('[data-sentiment-summary="selection"]')).toHaveCount(0);
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect(page.locator('[data-sentiment-summary="selection"] strong').first()).toContainText("۴ میلیون");
  await page.locator("#sentimentUnderlying").selectOption("2002");
  await expect(page.locator("#sentimentExpiry")).toHaveValue("all");
  await expect(page.locator('[data-sentiment-summary="underlying"]')).toContainText("بساما");
  await expect(page.locator('[data-sentiment-summary="selection"]')).toHaveCount(0);
  await page.locator("#sentimentDate").selectOption("2026-10-20");
  await expect(page.locator("#sentimentOiEmpty")).toContainText("موجود نیست");
  await expect(page.locator("#sentimentChangeEmpty")).toContainText("موجود نیست");
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 0);
  await expect(page.locator("#analysisPanel")).toBeVisible();
  assert.equal(state.posts.length, 1); // cached history never refreshes the live feed
});

test("late filter responses cannot overwrite a newer selection", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.handler = async (params) => params.get("expiry") === "20261209" && params.get("underlying_key") === "2001" ? pending : payload(params);
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect.poll(() => state.requests.at(-1).get("expiry")).toBe("20261209");
  await page.locator("#sentimentUnderlying").selectOption("2002");
  await expect(page.locator('[data-sentiment-summary="underlying"]')).toContainText("بساما");
  release(payload(new URLSearchParams({ underlying_key: "2001", expiry: "20261209" })));
  await page.waitForTimeout(100);
  await expect(page.locator("#sentimentUnderlying")).toHaveValue("2002");
  await expect(page.locator("#sentimentExpiry")).toHaveValue("all");
});

test("failed filter reads roll back controls and preserve the previous figures", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  const figures = await page.locator("#sentimentSummaries").textContent();
  state.failRead = true;
  await page.locator("#sentimentUnderlying").selectOption("2002");
  await expect(page.locator("#sentimentStatus")).toContainText("خطا");
  await expect(page.locator("#sentimentUnderlying")).toHaveValue("2001");
  assert.equal(await page.locator("#sentimentSummaries").textContent(), figures);
  state.failRead = false;
  state.failPost = true;
  await page.locator("#sentimentRefresh").click();
  await expect(page.locator("#sentimentStatus")).toContainText("خطا در تازه‌سازی");
  assert.equal(await page.locator("#sentimentSummaries").textContent(), figures);
});

test("a late historical response cannot restore an older date or mix summary scopes", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect(page.locator('[data-sentiment-summary="selection"] strong').first()).toContainText("۴ میلیون");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.handler = (params) => {
    if (params.get("date") === "2026-10-20") return pending;
    return payload(params, { summaries: {
      market: summary(40_000_000_000, 20_000_000_000),
      underlying: summary(200_000_000, 100_000_000),
      selection: summary(80_000_000, 40_000_000),
    } });
  };
  await page.locator("#sentimentDate").selectOption("2026-10-20");
  await expect.poll(() => state.requests.at(-1).get("date")).toBe("2026-10-20");
  await page.locator("#sentimentDate").selectOption("2026-10-21");
  await expect(page.locator('[data-sentiment-summary="market"] strong').first()).toContainText("۴ میلیارد");
  await expect(page.locator('[data-sentiment-summary="underlying"] strong').first()).toContainText("۲۰ میلیون");
  await expect(page.locator('[data-sentiment-summary="selection"] strong').first()).toContainText("۸ میلیون");
  const figures = await page.locator("#sentimentSummaries").textContent();
  const metadata = await page.locator("#sentimentMeta").textContent();
  release(payload(new URLSearchParams({ underlying_key: "2001", date: "2026-10-20" })));
  await page.waitForTimeout(100);
  await expect(page.locator("#sentimentDate")).toHaveValue("2026-10-21");
  assert.equal(await page.locator("#sentimentSummaries").textContent(), figures);
  assert.equal(await page.locator("#sentimentMeta").textContent(), metadata);
  await expect(page.locator("#analysisPanel")).toBeVisible();
  assert.equal(state.posts.length, 1);
});

test("theme changes, touch tooltips and tab lifecycle do not leak charts or timers", async (t) => {
  const { page, state, open } = await fixture(t, 360);
  await open();
  await page.evaluate(() => applyTheme("dark"));
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 2);
  await page.locator("#sentimentOiChart").scrollIntoViewIfNeeded();
  const point = await page.evaluate(() => {
    const chart = Chart.getChart("sentimentOiChart");
    const rect = chart.canvas.getBoundingClientRect(), bar = chart.getDatasetMeta(0).data[0];
    return { x: rect.x + bar.x, y: rect.y + (bar.y + bar.base) / 2 };
  });
  await page.touchscreen.tap(point.x, point.y);
  await page.clock.runFor(50);
  await expect.poll(() => page.evaluate(() => Chart.getChart("sentimentOiChart").tooltip.dataPoints?.length > 0)).toBeTruthy();
  const exact = page.locator('[data-sentiment-summary="underlying"] [data-exact-value]').first();
  await exact.focus();
  assert.match(await exact.evaluate((el) => getComputedStyle(el, "::after").content), /ریال/);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.getByRole("tab", { name: "حقیقی", exact: true }).click();
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 0);
  const count = state.requests.length;
  await page.clock.fastForward(10000);
  assert.equal(state.requests.length, count);
  await open();
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 2);
});

test("uncached historical data requests its own date and never changes to live", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  state.handler = (params) => payload(params, { cached: !params.get("date") || state.posts.some((p) => p.date === params.get("date")) });
  await page.locator("#sentimentDate").selectOption("2026-10-20");
  await expect.poll(() => state.posts.at(-1).date).toBe("2026-10-20");
  await expect(page.locator("#sentimentDate")).toHaveValue("2026-10-20");
});

test("mobile charts keep compact axes fixed, recenter on touch and adapt when resized", async (t) => {
  const { page, state, open } = await fixture(t);
  state.handler = (params) => {
    const result = payload(params);
    const template = result.strikes[0];
    result.strikes = Array.from({ length: 30 }, (_, i) => ({ ...template, strike_price: 1000 + i * 100, call_oi: 2_000_000 }));
    result.spot_price = 3000;
    result.atm_strike = 3000;
    return result;
  };
  await open();
  const scroller = page.locator("#sentimentOiChart").locator("../..");
  assert.ok(await scroller.evaluate((el) => el.scrollLeft > 0));
  assert.ok(await scroller.evaluate((el) => el.scrollWidth <= 30 * 44 + 1));
  assert.equal(await page.evaluate(() => Chart.getChart("sentimentOiChart").data.datasets[0].data.length), 30);
  const axis = page.locator("#sentimentOiAxis");
  const before = await axis.boundingBox();
  await scroller.evaluate((el) => { el.scrollLeft = 0; });
  const after = await axis.boundingBox();
  assert.equal(before.x, after.x);
  await expect(axis.locator("span")).not.toHaveCount(0);
  await expect(axis).toContainText("میلیون");
  await expect(page.locator("#sentimentOiHint")).toContainText("افقی بکشید");
  await page.locator('[data-center-chart="sentimentOiChart"]').tap();
  assert.ok(await scroller.evaluate((el) => el.scrollLeft > 0));
  const chart = await page.evaluate(() => {
    const chart = Chart.getChart("sentimentOiChart"), scroller = chart.canvas.parentElement.parentElement;
    return { axisEdge: document.querySelector("#sentimentOiAxis").getBoundingClientRect().right, scrollEdge: scroller.getBoundingClientRect().left,
      tickRotation: chart.scales.x.labelRotation,
      tooltip: chart.options.plugins.tooltip.callbacks.label({ datasetIndex: 0, dataIndex: 0, dataset: chart.data.datasets[0], parsed: {x: 1000, y: 2_000_000} }) };
  });
  assert.ok(chart.axisEdge <= chart.scrollEdge);
  assert.equal(chart.tickRotation, 0);
  assert.match(chart.tooltip, /۲٬۰۰۰٬۰۰۰ قرارداد/);
  await page.setViewportSize({ width: 320, height: 700 });
  await page.clock.runFor(100);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.setViewportSize({ width: 800, height: 900 });
  await page.clock.runFor(100);
  const offsets = await page.evaluate(() => {
    const chart = Chart.getChart("sentimentOiChart");
    return [...document.querySelectorAll("#sentimentOiAxis span")].map((el, i) => Math.abs(parseFloat(el.style.top) - chart.scales.y.getPixelForTick(i)));
  });
  assert.ok(offsets.every((offset) => offset < 1));
});

test("zero changes use a compact message while missing changes stay unavailable", async (t) => {
  const { page, state, open } = await fixture(t);
  let missing = false;
  state.handler = (params) => {
    const result = payload(params);
    result.strikes.forEach((row) => {
      for (const side of ["call", "put"]) {
        row[`${side}_change`] = missing ? null : 0;
        row.coverage[`${side}_change`] = metric(missing ? null : 0, missing ? 0 : 1);
      }
    });
    return result;
  };
  await open();
  await expect(page.locator("#sentimentChangeEmpty")).toContainText("صفر است");
  await expect(page.locator("#sentimentChangeChart")).toBeHidden();
  await expect(page.locator("#sentimentChangeFooter")).toBeHidden();
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 1);
  missing = true;
  await page.locator("#sentimentRefresh").click();
  await expect(page.locator("#sentimentChangeEmpty")).toContainText("موجود نیست");
  await expect(page.locator("#sentimentChangeEmpty")).not.toContainText("صفر است");
});

test("an error from a different refresh target cannot prevent loading historical data", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  state.handler = (params) => payload(params, {
    cached: !params.get("date") || state.posts.some((p) => p.date === params.get("date")),
    refresh: { running: false, last_error: "خطای دریافت آخرین داده", target_date: null },
  });
  await page.locator("#sentimentDate").selectOption("2026-10-20");
  await expect.poll(() => state.posts.at(-1).date).toBe("2026-10-20");
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
});

test("a stalled filter request times out, restores the previous scope and leaves retry usable", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.handler = (params) => params.get("expiry") === "20261209" ? pending : payload(params);
  await page.locator("#sentimentExpiry").selectOption("20261209");
  await expect.poll(() => state.requests.at(-1).get("expiry")).toBe("20261209");
  await page.clock.fastForward(16000);
  await expect(page.locator("#sentimentStatus")).toContainText("مهلت دریافت پاسخ");
  await expect(page.locator("#sentimentExpiry")).toHaveValue("all");
  await expect(page.locator("#sentimentRefresh")).toBeEnabled();
  release(payload(new URLSearchParams({ expiry: "20261209" })));
});

test("a lost start response reconciles the accepted server job without another POST", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let running = true;
  state.postHandler = () => pending;
  state.handler = (params) => payload(params, { refresh: { running, target_date: null }, received_at: running ? "2026-10-22T08:00:00+00:00" : "2026-10-22T08:05:00+00:00" });
  await page.locator("#sentimentRefresh").click();
  await expect.poll(() => state.posts.length).toBe(2);
  await page.clock.fastForward(16000);
  await expect(page.locator("#sentimentStatus")).toContainText("مهلت دریافت پاسخ");
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toContainText("در حال به‌روزرسانی کل بازار");
  running = false;
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  assert.equal(state.posts.length, 2);
  release({ status: "started" });
});

test("temporary polling failures preserve figures and recover without overlapping requests", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  const figures = await page.locator("#sentimentSummaries").textContent();
  state.refreshStatus = "started";
  await page.locator("#sentimentRefresh").click();
  state.failRead = true;
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toContainText("خطا");
  assert.equal(await page.locator("#sentimentSummaries").textContent(), figures);
  state.failRead = false;
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  assert.equal(state.posts.length, 2);
});

test("three failed status reads stop polling and leave manual retry available", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  state.refreshStatus = "started";
  await page.locator("#sentimentRefresh").click();
  state.failRead = true;
  const count = state.requests.length;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    await page.clock.fastForward(3100);
    await expect.poll(() => state.requests.length).toBe(count + attempt);
  }
  await page.clock.fastForward(20000);
  assert.equal(state.requests.length, count + 3);
  await expect(page.locator("#sentimentRefresh")).toBeEnabled();
  await expect(page.locator("#sentimentStatus")).toContainText("خطا");
});

test("manual retry starts a fresh polling budget after three failed status reads", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  state.refreshStatus = "started";
  state.failRead = true;
  await page.locator("#sentimentRefresh").click();
  const count = state.requests.length;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    await page.clock.fastForward(3100);
    await expect.poll(() => state.requests.length).toBe(count + attempt);
  }
  await expect(page.locator("#sentimentRefresh")).toBeEnabled();
  await page.locator("#sentimentRefresh").click();
  await expect.poll(() => state.posts.length).toBe(3);
  await page.clock.fastForward(3100);
  await expect.poll(() => state.requests.length).toBe(count + 4);
  state.failRead = false;
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  assert.equal(state.requests.length, count + 5);
});

test("entering latest mode queues its refresh behind a job for another date", async (t) => {
  const { page, state } = await fixture(t);
  let running = true;
  state.handler = (params) => payload(params, { refresh: { running, target_date: "2026-10-20" } });
  await page.getByRole("tab", { name: "سنتیمنت آپشن", exact: true }).click();
  await expect(page.locator("#sentimentStatus")).toContainText("در حال به‌روزرسانی");
  assert.equal(state.posts.length, 0);
  running = false;
  await page.clock.fastForward(3100);
  await expect.poll(() => state.posts.length).toBe(1);
  assert.deepEqual(state.posts[0], { date: null });
});

test("manual refresh is not lost when another date is already refreshing", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  let running = true;
  state.postHandler = () => ({ status: running ? "already_running" : "cached" });
  state.handler = (params) => payload(params, { refresh: { running, target_date: "2026-10-20" } });
  await page.locator("#sentimentRefresh").click();
  await expect.poll(() => state.posts.length).toBe(2);
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toContainText("در حال به‌روزرسانی");
  running = false;
  await page.clock.fastForward(3100);
  await expect.poll(() => state.posts.length).toBe(3);
  assert.deepEqual(state.posts.at(-1), { date: null });
});

test("an existing job for the same date satisfies entry without a duplicate refresh", async (t) => {
  const { page, state } = await fixture(t);
  let running = true;
  state.handler = (params) => payload(params, { refresh: { running, target_date: null } });
  await page.getByRole("tab", { name: "سنتیمنت آپشن", exact: true }).click();
  await expect(page.locator("#sentimentStatus")).toContainText("در حال به‌روزرسانی");
  running = false;
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  assert.equal(state.posts.length, 0);
});

test("a coalesced job that completes before polling does not enqueue another job", async (t) => {
  const { page, state, open } = await fixture(t);
  await open();
  state.postHandler = () => ({ status: "already_running" });
  state.handler = (params) => payload(params, { received_at: "2026-10-22T08:05:00+00:00", refresh: { running: false, target_date: null } });
  await page.locator("#sentimentRefresh").click();
  await expect.poll(() => state.posts.length).toBe(2);
  await page.clock.fastForward(3100);
  await expect(page.locator("#sentimentStatus")).toHaveText("تحلیل آماده است.");
  await page.clock.fastForward(61000);
  assert.equal(state.posts.length, 2);
});

test("a peak is marked partial when another strike has missing OI", async (t) => {
  const { page, state, open } = await fixture(t);
  state.handler = (params) => {
    const result = payload(params);
    result.peaks.call.scope_complete = false;
    return result;
  };
  await open();
  await expect(page.locator("#sentimentPeaks")).toContainText("داده ناقص؛ بیشینه در داده‌های موجود");
});

test("OI known only for an invalid strike cannot make an unknown chart look like zero", async (t) => {
  const { page, state, open } = await fixture(t);
  state.handler = (params) => {
    const result = payload(params);
    const row = { strike_price: 1000, call_oi: null, put_oi: 0, call_change: null, put_change: 0,
      coverage: { call_oi: metric(null, 0, 1), put_oi: metric(0, 0, 0), call_change: metric(null, 0, 1), put_change: metric(0, 0, 0) } };
    result.strikes = [row];
    result.quality = { ...result.quality, oi: metric(50, 1, 2), change: metric(5, 1, 2), invalid_strike_count: 1 };
    result.peaks = { call: null, put: null };
    return result;
  };
  await open();
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 0);
  await expect(page.locator("#sentimentOiEmpty")).toContainText("موجود نیست");
  await expect(page.locator("#sentimentChangeEmpty")).toContainText("موجود نیست");
  await expect(page.locator("#sentimentWarning")).toContainText("قیمت اعمال معتبر ندارد");
});

test("charts becoming available within the same date center the spot price on mobile", async (t) => {
  const { page, state, open } = await fixture(t);
  let hasOi = false;
  state.handler = (params) => {
    const result = payload(params);
    result.spot_price = 3500;
    result.atm_strike = 3500;
    result.strikes = Array.from({ length: 30 }, (_, index) => ({
      strike_price: 1000 + index * 100, call_oi: hasOi ? 100 : null, put_oi: 0,
      call_change: hasOi ? 5 : null, put_change: 0,
      coverage: { call_oi: metric(hasOi ? 100 : null, hasOi ? 1 : 0, 1), put_oi: metric(0, 0, 0),
        call_change: metric(hasOi ? 5 : null, hasOi ? 1 : 0, 1), put_change: metric(0, 0, 0) },
    }));
    result.quality.oi = metric(hasOi ? 3000 : null, hasOi ? 30 : 0, 30);
    result.quality.change = metric(hasOi ? 150 : null, hasOi ? 30 : 0, 30);
    return result;
  };
  await open();
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 0);
  hasOi = true;
  await page.locator("#sentimentRefresh").click();
  await expect(page.locator("#sentimentOiChart")).toBeVisible();
  const position = await page.evaluate(() => {
    const chart = Chart.getChart("sentimentOiChart"), scroller = chart.canvas.parentElement.parentElement;
    return { left: scroller.scrollLeft, spot: chart.scales.x.getPixelForValue(3500), width: scroller.clientWidth };
  });
  assert.ok(position.left > 0);
  assert.ok(position.spot > position.left + 52 && position.spot < position.left + position.width);
});

test("historical contract data never receives live or cached client types from another day", async (t) => {
  const { page, state } = await fixture(t);
  const result = await page.evaluate(async () => {
    state.selectedDate = "2026-10-21";
    const row = {ins_code: "1", natural_buy_volume: 12};
    state.items = [row];
    state.selectedInsCode = "1";
    state.clientTypeByInsCode["2026-10-22:1"] = {natural_buy_volume: 999};
    applyCachedClientTypes(state.items);
    await loadClientTypesForRows(state.items);
    await loadClientTypeForSelected(row);
    return row.natural_buy_volume;
  });
  assert.equal(result, 12);
  assert.equal(state.clientTypeRequests.length, 0);
});

test("live client-type batches respect the server limit and ignore stale record dates", async (t) => {
  const { page, state } = await fixture(t);
  state.clientTypeHandler = (codes) => ({items: codes.map((ins_code) => ({ins_code,
    rec_date: ins_code === "1" ? 20261021 : 20261022, natural_buy_volume: 99}))});
  const result = await page.evaluate(async () => {
    state.selectedDate = "2026-10-22";
    state.items = Array.from({length: 301}, (_, index) => ({ins_code: String(index + 1), natural_buy_volume: 12}));
    await loadClientTypesForRows(state.items);
    return [state.items[0].natural_buy_volume, state.items[300].natural_buy_volume];
  });
  assert.deepEqual(state.clientTypeRequests.map((codes) => codes.length), [300, 1]);
  assert.deepEqual(result, [12, 99]);
});

test("a pending live client-type response cannot update the page after the selected date changes", async (t) => {
  const { page, state } = await fixture(t);
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.clientTypeHandler = () => pending;
  await page.evaluate(() => {
    state.selectedDate = "2026-10-22";
    state.items = [{ins_code: "1", natural_buy_volume: 12}];
    window.pendingClientTypes = loadClientTypesForRows(state.items);
  });
  await expect.poll(() => state.clientTypeRequests.length).toBe(1);
  await page.evaluate(() => { state.selectedDate = "2026-10-21"; });
  release({items: [{ins_code: "1", natural_buy_volume: 999}]});
  await page.evaluate(() => window.pendingClientTypes);
  assert.equal(await page.evaluate(() => state.items[0].natural_buy_volume), 12);
});

test("incomplete OI and client volumes stay unknown in daily analysis", async (t) => {
  const { page } = await fixture(t);
  const result = await page.evaluate(() => {
    const rows = [{option_type: "call", moneyness: "ITM", buy_open_positions: 100, yesterday_open_positions: 90},
      {option_type: "call", moneyness: "ITM", buy_open_positions: 100, yesterday_open_positions: null}];
    const snapshot = openInterestSnapshot(rows);
    const analysis = buildFourStepConclusion(rows, "natural", "حقیقی", "natural");
    return {snapshot, score: analysis.score, label: analysis.finalLabel,
      zeroOi: openInterestValue({buy_open_positions: 0, sell_open_positions: 20})};
  });
  assert.equal(result.snapshot.current, 200);
  assert.equal(result.snapshot.change, null);
  assert.equal(result.score, null);
  assert.match(result.label, /کامل نیست/);
  assert.equal(result.zeroOi, 0);
});

test("daily participant scores agree with the historical score for puts", async (t) => {
  const { page } = await fixture(t);
  const result = await page.evaluate(() => buildFourStepConclusion([{
    option_type: "put", moneyness: "OTM", natural_buy_volume: 50, natural_sell_volume: 50,
    buy_open_positions: 22, yesterday_open_positions: 20,
  }], "natural", "حقیقی", "natural").score);
  assert.equal(result, -3);
});

test("Persian decimals and Tehran timestamps format correctly outside Iran", async (t) => {
  const { page } = await fixture(t);
  const result = await page.evaluate(() => ({
    invalid: [true, false, [], {}, " "].map(asFiniteNumber),
    decimal: asFiniteNumber("۱٬۲۳۴٫۵"),
    dateOnly: fmtDate("2026-10-22"), dateNumeric: fmtDate(20261022),
    time: fmtDate("2026-10-22T08:00:00Z"), naiveTime: fmtDate("2026-10-22T08:00:00"),
    midnight: localIsoDate(new Date("2026-10-21T21:00:00Z")),
  }));
  assert.deepEqual(result.invalid, [null, null, null, null, null]);
  assert.equal(result.decimal, 1234.5);
  // Different formatters can pad the Jalali month/day differently.
  const dateParts = (date) => date.replace(/[۰-۹]/g, (digit) => String(digit.charCodeAt(0) - 0x06f0)).split("/").map(Number);
  assert.deepEqual(dateParts(result.dateOnly), dateParts(result.dateNumeric));
  assert.equal(result.time, result.naiveTime);
  assert.match(result.time, /۱۱:۳۰/);
  assert.equal(result.midnight, "2026-10-22");
});
