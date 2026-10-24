/** Render the real app with the existing offline regression fixtures.
 * npm run review:visual --prefix tests/browser -- before|after
 * Screenshots and measurements are written to ignored output/ui-redesign/.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { execFileSync } = require('node:child_process');

const stage = process.argv[2] || 'after';
if (!['before', 'after'].includes(stage)) throw new Error('Expected before or after');
const output = path.resolve(__dirname, '../../output/ui-redesign', stage);
const teardowns = [];
const report = [];
const contrastReport = [];

// Reuse fixtures without registering or running the regression tests themselves.
async function loadFixture(name) {
  const filename = path.join(__dirname, `${name}.spec.cjs`);
  const setup = [];
  const localRequire = createRequire(filename);
  const fixtureFs = localRequire('node:fs/promises');
  const baseline = new Map();
  const context = {
    require: (id) => id === 'node:test'
      ? { test() {}, before: (fn) => setup.push(fn), after: (fn) => teardowns.push(fn) }
      : id === 'node:fs/promises' && stage === 'before'
      ? { ...fixtureFs, readFile: async (file, ...args) => {
        const relative = path.relative(path.resolve(__dirname, '../..'), file);
        if (!relative.startsWith('src/options/web/')) return fixtureFs.readFile(file, ...args);
        if (!baseline.has(relative)) {
          baseline.set(relative, execFileSync('git', ['show', `HEAD:${relative}`], { cwd: path.resolve(__dirname, '../..') }));
        }
        return baseline.get(relative);
      } }
      : localRequire(id),
    __dirname, process, console, URL, URLSearchParams, setTimeout, clearTimeout,
    module: { exports: {} },
  };
  vm.runInNewContext(`${await fs.readFile(filename, 'utf8')}\nmodule.exports = fixture;`, context, { filename });
  for (const fn of setup) await fn();
  return context.module.exports;
}

async function measure(page, name, width, theme) {
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => {
    const visible = (el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    return {
      pageWidth: innerWidth,
      contentWidth: document.documentElement.scrollWidth,
      controls: [...document.querySelectorAll('button, select, input:not([type=hidden]):not([type=checkbox]), summary, .btn')]
        .filter(visible)
        .map((el) => ({ id: el.id || el.className, calendarDay: el.matches('.date-picker-day'), height: Math.round(el.getBoundingClientRect().height), width: Math.round(el.getBoundingClientRect().width) }))
        // Secondary day cells keep all seven columns visible on small phones.
        .filter((el) => el.height < 48 || (el.width < 48 && !el.calendarDay)),
    };
  });
  report.push({ name, width, theme, ...result });
  assert.ok(result.contentWidth <= result.pageWidth, `${name}/${width}/${theme}: page overflows`);
  if (stage === 'after') assert.deepEqual(result.controls, [], `${name}/${width}/${theme}: small touch targets`);
}

async function snapshot(page, name, theme) {
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelector('.app').scrollTop = 0; });
  await page.screenshot({ path: path.join(output, `${name}_${theme}.png`), animations: 'disabled' });
}

async function setTheme(page, theme) {
  await page.evaluate((value) => {
    localStorage.setItem('options-theme', value);
    if (typeof applyTheme === 'function') applyTheme(value);
    else document.documentElement.dataset.theme = value;
  }, theme);
}

async function checkContrast(page, theme) {
  const colors = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(['text', 'text-muted', 'surface', 'surface-soft', 'bg-0', 'accent', 'accent-strong', 'accent-soft', 'on-accent', 'green', 'green-bg', 'red', 'red-bg', 'amber', 'amber-bg', 'purple', 'purple-bg']
      .map((key) => [key, style.getPropertyValue(`--${key}`).trim()]));
  });
  const luminance = (color) => {
    assert.match(color, /^#[a-f\d]{6}$/i);
    return [1, 3, 5].map((index) => parseInt(color.slice(index, index + 2), 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  };
  for (const [foreground, background] of [['text', 'bg-0'], ['text', 'surface'], ['text-muted', 'surface-soft'], ['accent-strong', 'accent-soft'], ['on-accent', 'accent'], ['on-accent', 'accent-strong'], ['green', 'green-bg'], ['red', 'red-bg'], ['amber', 'amber-bg'], ['purple', 'purple-bg']]) {
    const values = [luminance(colors[foreground]), luminance(colors[background])].sort((a, b) => a - b);
    const ratio = (values[1] + 0.05) / (values[0] + 0.05);
    assert.ok(ratio >= 4.5, `${theme}/${foreground}/${background}: contrast ${ratio}`);
    contrastReport.push({ theme, foreground, background, ratio: Math.round(ratio * 100) / 100 });
  }
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  const watchlist = await loadFixture('watchlist');
  const analysis = await loadFixture('option-sentiment');
  const expiry = await loadFixture('expiries');
  const t = { after: (fn) => teardowns.push(fn) };

  for (const theme of ['light', 'dark']) {
    const home = await watchlist(t, 'dashboard');
    home.state.summary = { underlying_count: 3, contract_count: 124, call_count: 84, put_count: 40, total_trade_volume: 1254600 };
    home.state.underlyingsHandler = () => ['خودرو', 'فملی', 'بساما'].map((symbol, index) => ({
      underlying_key: String(2001 + index), underlying_ins_code: String(2001 + index), underlying_symbol: symbol,
      underlying_short_name: ['ایران خودرو', 'ملی صنایع مس ایران', 'بیمه سامان'][index], available: true,
      data_status: 'ready', contract_count: 24, call_count: 16, put_count: 8, nearest_end_date: 20261209, trade_volume: 125400,
    }));
    await home.state.open();
    await setTheme(home.page, theme);
    await home.page.locator('.mobile-data-card').first().click();
    await snapshot(home.page, 'dashboard', theme);

    const editor = await watchlist(t, 'symbols');
    await editor.state.open();
    await setTheme(editor.page, theme);
    await editor.page.locator('#symbolList input').check();
    await snapshot(editor.page, 'symbols', theme);

    const detail = await analysis(t);
    await setTheme(detail.page, theme);
    await detail.page.locator('#contextAnalysisButton').click();
    await snapshot(detail.page, 'contracts', theme);
    for (const width of [320, 360, 390, 430, 1280]) {
      await detail.page.setViewportSize({ width, height: 844 });
      await measure(detail.page, 'contracts', width, theme);
    }
    await detail.page.setViewportSize({ width: 390, height: 844 });
    await detail.page.locator('#contextAnalysisButton').click();
    await snapshot(detail.page, 'analysis', theme);
    for (const width of [320, 360, 390, 430, 1280]) {
      await detail.page.setViewportSize({ width, height: 844 });
      await measure(detail.page, 'analysis', width, theme);
    }
    await detail.page.setViewportSize({ width: 390, height: 844 });
    await detail.open();
    await snapshot(detail.page, 'sentiment', theme);

    const expiries = await expiry(t);
    await expiries.state.open();
    await setTheme(expiries.page, theme);
    await expiries.page.locator('#btnExpiries').click();
    await expiries.page.locator('#expiryFilterToggle').click();
    await snapshot(expiries.page, 'expiries', theme);

    for (const width of [320, 360, 390, 430, 1280]) {
      for (const [name, page] of [['dashboard', home.page], ['symbols', editor.page], ['sentiment', detail.page], ['expiries', expiries.page]]) {
        await page.setViewportSize({ width, height: 844 });
        await measure(page, name, width, theme);
      }
    }
    if (stage === 'after') {
      await checkContrast(home.page, theme);
      await home.page.setViewportSize({ width: 320, height: 844 });
      await home.page.locator('#datePickerButton').click();
      await measure(home.page, 'calendar', 320, theme);
      await home.page.locator('#datePickerButton').click();
      await home.page.evaluate(() => setActivationGate(true));
      await measure(home.page, 'activation', 320, theme);
      await home.page.locator('#activationOverlay').screenshot({ path: path.join(output, `activation_${theme}.png`) });

      // Exercise long content, incomplete data and empty/error/loading layouts.
      const edge = await watchlist(t, 'dashboard');
      const coverage = { known: 1, total: 2, complete: false };
      edge.state.summary = { underlying_count: 1, contract_count: 9876543210, total_trade_volume: null, coverage: { total_trade_volume: coverage } };
      edge.state.underlyingsHandler = () => [{ underlying_key: '2001', underlying_ins_code: '2001', underlying_symbol: 'خودرو', underlying_short_name: 'نام طولانی دارایی پایه برای بررسی خوانایی اطلاعات در نمایشگر کوچک', available: true, data_status: 'ready', contract_count: 9876543210, call_count: 9876543210, put_count: 1234567890, trade_volume: null, coverage: { trade_volume: coverage } }];
      await edge.state.open();
      await setTheme(edge.page, theme);
      await edge.page.setViewportSize({ width: 320, height: 844 });
      await edge.page.locator('.mobile-data-card').click();
      await measure(edge.page, 'long-and-partial', 320, theme);
      const clipped = await edge.page.locator('.stat-value, .mobile-metric strong, .mobile-card-head strong, .mobile-card-head small').evaluateAll((elements) => elements.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent));
      assert.deepEqual(clipped, [], 'Long values must wrap without clipping');
      edge.state.underlyingsHandler = () => [];
      await edge.page.reload();
      await edge.page.locator('#emptyState:not(.hidden)').waitFor();
      await measure(edge.page, 'empty', 320, theme);
      await edge.page.evaluate(() => {
        document.getElementById('refreshWarning').textContent = 'دریافت اطلاعات کامل نشد؛ داده‌های قبلی حفظ شده‌اند.';
        document.getElementById('refreshWarning').classList.remove('hidden');
        setLoading(true);
      });
      await measure(edge.page, 'loading-and-warning', 320, theme);
      await edge.page.emulateMedia({ reducedMotion: 'reduce' });
      const duration = await edge.page.locator('#btnRefresh').evaluate((el) => getComputedStyle(el).transitionDuration);
      assert.ok(duration.split(',').every((value) => parseFloat(value) <= 0.01), 'Reduced motion must disable transitions');
    }
  }
  await fs.writeFile(path.join(output, 'measurements.json'), JSON.stringify(report, null, 2));
  if (contrastReport.length) await fs.writeFile(path.join(output, 'contrast.json'), JSON.stringify(contrastReport, null, 2));
  console.log(`${report.length} viewport/theme checks and ${contrastReport.length} contrast checks passed. Screenshots: ${output}`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  for (const fn of teardowns.reverse()) await fn();
});
