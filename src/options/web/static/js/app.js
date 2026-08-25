/**
 * TSETMC Options Dashboard — frontend
 */

const underlyingMatch = window.location.pathname.match(/^\/underlying\/(.+)$/);
const initialParams = new URLSearchParams(window.location.search);

function translateDigits(value) {
  return String(value || "")
    .replace(/[۰-۹]/g, (digit) => String(digit.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (digit) => String(digit.charCodeAt(0) - 0x0660));
}

const state = {
  view: underlyingMatch ? "underlying" : "underlyings",
  underlyingKey: underlyingMatch ? decodeURIComponent(underlyingMatch[1]) : null,
  selectedDate: validIsoDate(initialParams.get("date") || "") ? translateDigits(initialParams.get("date") || "") : "",
  latestDate: "",
  lastUpdate: "",
  availableDates: [],
  calendarMonths: {},
  calendarVisible: false,
  calendarView: null,
  calendarTodayIso: "",
  calendarRequestId: 0,
  dateChangeRequestId: 0,
  items: [],
  filtered: [],
  sortKey: null,
  sortDir: 1,
  selectedInsCode: null,
  selectedRowKey: null,
  clientTypeRequestId: 0,
  clientTypeBatchRequestId: 0,
  clientTypeByInsCode: {},
  expandedCardKey: null,
  oiChart: null,
  oiRequestId: 0,
  trendChart: null,
  underlying: null,
  analysisVisible: false,
  analysisAudience: "both",
  trendMetricGroup: "score",
  trendRequestId: 0,
  loading: false,
  activated: false,
  filters: {
    type: "all",
    expiry: "all",
    moneyness: "all",
    strike: "all",
  },
};

const THEME_STORAGE_KEY = "options-theme";
const API_TOKEN_COOKIE = "options_api_token";

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Some embedded webviews can block localStorage; theme still works for this session.
  }
}

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function chartPalette() {
  return {
    text: cssVar("--text", "#142033"),
    muted: cssVar("--text-muted", "#6b7a8c"),
    grid: currentTheme() === "dark" ? "rgba(148, 163, 184, 0.16)" : "rgba(107, 122, 140, 0.14)",
    accent: cssVar("--accent", "#0f8b8d"),
    accentStrong: cssVar("--accent-strong", "#0a7375"),
    green: cssVar("--green", "#16885c"),
    red: cssVar("--red", "#c44d61"),
    amber: cssVar("--amber", "#a86f05"),
    purple: cssVar("--purple", "#6d5bd0"),
  };
}

function applyTheme(theme) {
  const nextTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = nextTheme;
  const meta = document.getElementById("themeColorMeta");
  if (meta) meta.setAttribute("content", nextTheme === "dark" ? "#0b1220" : "#f7f8fb");
  const toggle = document.getElementById("themeToggle");
  if (toggle) {
    const isDark = nextTheme === "dark";
    toggle.setAttribute("aria-pressed", String(isDark));
    toggle.setAttribute("aria-label", isDark ? "تغییر به حالت روشن" : "تغییر به حالت تاریک");
    toggle.title = isDark ? "حالت روشن" : "حالت تاریک";
  }
  if (state.oiChart || state.trendChart) {
    requestAnimationFrame(() => {
      if (state.selectedInsCode) loadOiChart(state.selectedInsCode);
      if (state.trendChart && state.analysisVisible) renderAnalysis();
    });
  }
}

function bindThemeToggle() {
  applyTheme(currentTheme());
  document.getElementById("themeToggle")?.addEventListener("click", () => {
    const nextTheme = currentTheme() === "dark" ? "light" : "dark";
    safeStorageSet(THEME_STORAGE_KEY, nextTheme);
    applyTheme(nextTheme);
  });
}

const VIEW_CONFIG = {
  underlyings: {
    title: "سهم‌های اصلی",
    searchPlaceholder: "جستجو سهم...",
    columns: [
      { key: "underlying_symbol", label: "سهم" },
      { key: "underlying_short_name", label: "نام" },
      { key: "contract_count", label: "قرارداد", fmt: "num" },
      { key: "call_count", label: "اختیار خرید", fmt: "num" },
      { key: "put_count", label: "اختیار فروش", fmt: "num" },
      { key: "nearest_end_date", label: "نزدیک‌ترین سررسید", fmt: "date" },
      { key: "min_strike_price", label: "کمترین اعمال", fmt: "num" },
      { key: "max_strike_price", label: "بیشترین اعمال", fmt: "num" },
      { key: "trade_volume", label: "حجم", fmt: "num" },
      { key: "trade_value", label: "ارزش", fmt: "num" },
    ],
  },
  underlying: {
    title: "قراردادهای اختیار معامله",
    searchPlaceholder: "جستجو قرارداد...",
    columns: [
      { key: "option_type", label: "نوع", fmt: "optionType" },
      { key: "symbol", label: "نماد قرارداد" },
      { key: "short_name", label: "نام" },
      { key: "strike_price", label: "اعمال", fmt: "num" },
      { key: "end_date", label: "سررسید", fmt: "date" },
      { key: "moneyness", label: "وضعیت" },
      { key: "last_price", label: "آخرین", fmt: "num" },
      { key: "closing_price", label: "پایانی", fmt: "num" },
      { key: "trade_volume", label: "حجم", fmt: "num" },
      { key: "buy_open_positions", label: "موقعیت باز", fmt: "num" },
    ],
  },
};

document.body.dataset.view = state.view;

const DETAIL_LABELS = {
  ins_code: "کد نماد",
  symbol: "نماد",
  short_name: "نام کوتاه",
  long_name: "نام کامل",
  strike_price: "قیمت اعمال",
  end_date: "سررسید",
  contract_size: "اندازه قرارداد",
  underlying_symbol: "دارایی پایه",
  underlying_short_name: "نام دارایی پایه",
  underlying_ins_code: "کد دارایی پایه",
  underlying_last_price: "آخرین قیمت دارایی پایه",
  underlying_closing_price: "قیمت پایانی دارایی پایه",
  last_price: "آخرین قیمت",
  closing_price: "قیمت پایانی",
  price_change: "تغییر قیمت",
  trade_volume: "حجم معاملات",
  trade_value: "ارزش معاملات",
  total_buy_volume: "حجم خرید کل",
  total_sell_volume: "حجم فروش کل",
  open_interest_positions: "موقعیت باز موثر",
  open_interest_change: "تغییر موقعیت باز",
  buy_open_positions: "موقعیت باز خرید",
  sell_open_positions: "موقعیت باز فروش",
  yesterday_open_positions: "موقعیت دیروز",
  natural_money_flow: "جریان پول حقیقی",
  legal_money_flow: "جریان پول حقوقی",
  natural_buy_count: "تعداد خریدار حقیقی",
  natural_buy_volume: "حجم خرید حقیقی",
  natural_buy_value: "ارزش خرید حقیقی",
  natural_sell_count: "تعداد فروشنده حقیقی",
  natural_sell_volume: "حجم فروش حقیقی",
  natural_sell_value: "ارزش فروش حقیقی",
  legal_buy_count: "تعداد خریدار حقوقی",
  legal_buy_volume: "حجم خرید حقوقی",
  legal_buy_value: "ارزش خرید حقوقی",
  legal_sell_count: "تعداد فروشنده حقوقی",
  legal_sell_volume: "حجم فروش حقوقی",
  legal_sell_value: "ارزش فروش حقوقی",
  option_type: "نوع قرارداد",
  moneyness: "وضعیت ITM/OTM",
  intrinsic_value: "ارزش ذاتی",
  market_name: "بازار",
  sector: "صنعت",
  contract_count: "تعداد قراردادها",
  call_count: "اختیار خرید",
  put_count: "اختیار فروش",
  nearest_end_date: "نزدیک‌ترین سررسید",
  min_strike_price: "کمترین قیمت اعمال",
  max_strike_price: "بیشترین قیمت اعمال",
};

function asFiniteNumber(value) {
  if (value == null || value === "") return null;
  const number = Number(typeof value === "string" ? cleanNumericText(value) : value);
  return Number.isFinite(number) ? number : null;
}

function cleanNumericText(value) {
  return value
    .trim()
    .replace(/[۰-۹٠-٩]/g, (digit) => translateDigits(digit))
    .replaceAll(",", "")
    .replaceAll("٬", "")
    .replaceAll("،", "")
    .replaceAll(" ", "");
}

function fmtNum(n) {
  const number = asFiniteNumber(n);
  if (number == null) return "—";
  return number.toLocaleString("fa-IR", { maximumFractionDigits: 0 });
}

function fmtDate(d) {
  if (!d) return "—";
  const s = String(d);
  if (s.length === 8 && /^\d+$/.test(s)) {
    const year = Number(s.slice(0, 4));
    const month = Number(s.slice(4, 6));
    const day = Number(s.slice(6, 8));
    if (year >= 1700) {
      const date = new Date(year, month - 1, day);
      if (
        date.getFullYear() === year
        && date.getMonth() === month - 1
        && date.getDate() === day
      ) {
        const { jy, jm, jd } = gregorianToJalali(year, month, day);
        const part = (value, digits = 2) => value.toLocaleString("fa-IR", {
          minimumIntegerDigits: digits,
          useGrouping: false,
        });
        return `${part(jy, 4)}/${part(jm)}/${part(jd)}`;
      }
    }
    return `${s.slice(0, 4)}/${s.slice(4, 6)}/${s.slice(6, 8)}`;
  }
  try {
    const date = new Date(d);
    if (Number.isNaN(date.getTime())) return s;
    const hasTime = /[T:\s]\d{1,2}:/.test(s);
    return date.toLocaleString("fa-IR", hasTime ? { dateStyle: "short", timeStyle: "short" } : { dateStyle: "short" });
  } catch {
    return s;
  }
}

const PERSIAN_MONTHS = [
  "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
  "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
];

function div(a, b) {
  return ~~(a / b);
}

function jalaliToGregorian(jy, jm, jd) {
  jy += 1595;
  let days = -355668 + (365 * jy) + div(jy, 33) * 8 + div((jy % 33) + 3, 4) + jd;
  days += jm < 7 ? (jm - 1) * 31 : ((jm - 7) * 30) + 186;
  let gy = 400 * div(days, 146097);
  days %= 146097;
  if (days > 36524) {
    gy += 100 * div(days - 1, 36524);
    days = (days - 1) % 36524;
    if (days >= 365) days += 1;
  }
  gy += 4 * div(days, 1461);
  days %= 1461;
  if (days > 365) {
    gy += div(days - 1, 365);
    days = (days - 1) % 365;
  }
  let gd = days + 1;
  const leap = gy % 4 === 0 && (gy % 100 !== 0 || gy % 400 === 0);
  const salA = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let gm = 0;
  while (gm < 12 && gd > salA[gm]) {
    gd -= salA[gm];
    gm += 1;
  }
  return { gy, gm: gm + 1, gd };
}

function gregorianToJalali(gy, gm, gd) {
  const gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  let jy = gy <= 1600 ? 0 : 979;
  gy -= gy <= 1600 ? 621 : 1600;
  const gy2 = gm > 2 ? gy + 1 : gy;
  let days = (365 * gy) + div(gy2 + 3, 4) - div(gy2 + 99, 100) + div(gy2 + 399, 400) - 80 + gd + gdm[gm - 1];
  jy += 33 * div(days, 12053);
  days %= 12053;
  jy += 4 * div(days, 1461);
  days %= 1461;
  if (days > 365) {
    jy += div(days - 1, 365);
    days = (days - 1) % 365;
  }
  const jm = days < 186 ? 1 + div(days, 31) : 7 + div(days - 186, 30);
  const jd = 1 + (days < 186 ? days % 31 : (days - 186) % 30);
  return { jy, jm, jd };
}

function isJalaliLeapYear(year) {
  const breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];
  let leapJ = -14;
  let jp = breaks[0];
  let jump = 0;
  for (const jm of breaks.slice(1)) {
    jump = jm - jp;
    if (year < jm) break;
    leapJ += div(jump, 33) * 8 + div(jump % 33, 4);
    jp = jm;
  }
  let n = year - jp;
  leapJ += div(n, 33) * 8 + div((n % 33) + 3, 4);
  if (jump % 33 === 4 && jump - n === 4) leapJ += 1;
  if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
  let leap = ((n + 1) % 33 - 1) % 4;
  if (leap === -1) leap = 4;
  return leap === 0;
}

function jalaliMonthLength(year, month) {
  if (month <= 6) return 31;
  if (month <= 11) return 30;
  return isJalaliLeapYear(year) ? 30 : 29;
}

function isoDate(gy, gm, gd) {
  return `${gy}-${String(gm).padStart(2, "0")}-${String(gd).padStart(2, "0")}`;
}

function localIsoDate(date = new Date()) {
  return isoDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

function calendarTodayIso() {
  return validIsoDate(state.calendarTodayIso) ? state.calendarTodayIso : localIsoDate();
}

function validIsoDate(value) {
  const normalized = translateDigits(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return false;
  const [year, month, day] = normalized.split("-").map(Number);
  const parsed = new Date(year, month - 1, day);
  return parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day;
}

function selectedJalaliDate() {
  const preferred = translateDigits(state.selectedDate || "");
  const latest = translateDigits(state.latestDate || "");
  const today = calendarTodayIso();
  const base = validIsoDate(preferred) ? preferred : validIsoDate(latest) ? latest : today;
  const [gy, gm, gd] = base.split("-").map(Number);
  return gregorianToJalali(gy, gm, gd);
}

function fmtFlow(n) {
  const number = asFiniteNumber(n);
  if (number == null) return "—";
  const formatted = fmtNum(number);
  return number > 0 ? `+${formatted}` : formatted;
}

function fmtPct(n, multiplier = 100) {
  const number = asFiniteNumber(n);
  if (number == null) return "—";
  return `${(number * multiplier).toLocaleString("fa-IR", { maximumFractionDigits: 1 })}٪`;
}

function fmtRatio(n) {
  const number = asFiniteNumber(n);
  if (number == null) return "—";
  return number.toLocaleString("fa-IR", { maximumFractionDigits: 2 });
}

function fmtCompactNum(n) {
  const number = asFiniteNumber(n);
  if (number == null) return "—";
  const value = Math.abs(number);
  const sign = number < 0 ? "-" : "";
  if (value >= 1_000_000_000) {
    return `${sign}${(value / 1_000_000_000).toLocaleString("fa-IR", { maximumFractionDigits: 1 })} میلیارد`;
  }
  if (value >= 1_000_000) {
    return `${sign}${(value / 1_000_000).toLocaleString("fa-IR", { maximumFractionDigits: 1 })} میلیون`;
  }
  if (value >= 10_000) {
    return `${sign}${(value / 1_000).toLocaleString("fa-IR", { maximumFractionDigits: 0 })} هزار`;
  }
  return fmtNum(n);
}

function optionTypeLabel(value) {
  if (value === "call") return "اختیار خرید";
  if (value === "put") return "اختیار فروش";
  return "—";
}

function formatCell(col, val) {
  if (col.fmt === "num") return fmtNum(val);
  if (col.fmt === "date") return fmtDate(val);
  if (col.fmt === "flow") return fmtFlow(val);
  if (col.fmt === "pct") return fmtPct(val);
  if (col.fmt === "pct0") return fmtPct(val, 1);
  if (col.fmt === "ratio") return fmtRatio(val);
  if (col.fmt === "optionType") return optionTypeLabel(val);
  return val ?? "—";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function optionTypeClass(value) {
  if (value === "call") return "type-call";
  if (value === "put") return "type-put";
  return "type-unknown";
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function showToast(msg, type = "success") {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = `toast ${type}`;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add("hidden"), 3500);
}

function setLoading(on) {
  state.loading = on;
  document.getElementById("loadingOverlay")?.classList.toggle("hidden", !on);
  renderLoadingSkeleton(on);
}

function setStatusText(text) {
  if (text) setText("lastUpdate", text);
}

function setEmptyMessage(title, hint) {
  const emptyTitle = document.getElementById("emptyTitle");
  const emptyHint = document.getElementById("emptyHint");
  if (emptyTitle) emptyTitle.textContent = title;
  if (emptyHint) emptyHint.textContent = hint;
}

function isHistoricalDate() {
  return Boolean(state.selectedDate && state.latestDate && state.selectedDate !== state.latestDate);
}

function updateFreshnessBadge() {
  const el = document.getElementById("lastUpdate");
  if (!el) return;
  el.classList.toggle("stale", isHistoricalDate());
  if (isHistoricalDate() && !el.textContent.includes("داده تاریخی")) {
    el.textContent = `${el.textContent} · داده تاریخی`;
  }
}

function dateQuery(prefix = "?") {
  return state.selectedDate ? `${prefix}date=${encodeURIComponent(state.selectedDate)}` : "";
}

function goBackToUnderlyings() {
  window.location.assign(`/${dateQuery()}`);
}

function openUnderlyingPage(row) {
  const key = row?.underlying_key || row?.underlying_ins_code || row?.underlying_symbol;
  if (!key) {
    showToast("شناسه سهم برای باز کردن صفحه موجود نیست", "error");
    return;
  }
  window.location.assign(`/underlying/${encodeURIComponent(key)}${dateQuery()}`);
}

function isMobileLayout() {
  return true;
}

function appendQuery(params) {
  const query = new URLSearchParams();
  if (state.selectedDate) query.set("date", state.selectedDate);
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const s = query.toString();
  return s ? `?${s}` : "";
}

function syncDateToUrl() {
  const url = new URL(window.location.href);
  if (state.selectedDate) url.searchParams.set("date", state.selectedDate);
  else url.searchParams.delete("date");
  window.history.replaceState({}, "", `${url.pathname}${url.search}`);
}

function readCookie(name) {
  const prefix = `${name}=`;
  return document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) || "";
}

async function api(path, options = {}) {
  const token = readCookie(API_TOKEN_COOKIE);
  const headers = {
    "Content-Type": "application/json",
    ...(token ? { "X-Options-Api-Token": token } : {}),
    ...(options.headers || {}),
  };
  const res = await fetch(path, {
    ...options,
    headers,
  });
  const text = await res.text();
  if (!res.ok) {
    let message = text || res.statusText;
    try {
      const parsed = text ? JSON.parse(text) : null;
      message = parsed?.detail || parsed?.message || message;
    } catch {
      // Keep the raw response text when the server/proxy does not return JSON.
    }
    throw new Error(message);
  }
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("پاسخ نامعتبر از سرور");
  }
}

function setActivationGate(locked, message = "") {
  const overlay = document.getElementById("activationOverlay");
  const input = document.getElementById("activationCode");
  const error = document.getElementById("activationError");
  overlay?.classList.toggle("hidden", !locked);
  if (error) {
    error.textContent = message || "کد پذیرفته نشد";
    error.classList.toggle("hidden", !message);
  }
  if (locked) {
    requestAnimationFrame(() => input?.focus());
  }
}

async function ensureActivation() {
  const status = await api("/api/activation/status");
  state.activated = Boolean(status.activated);
  setActivationGate(!state.activated);
  return state.activated;
}

async function submitActivationCode(event) {
  event.preventDefault();
  const input = document.getElementById("activationCode");
  const submit = document.getElementById("activationSubmit");
  const code = input?.value.trim() || "";
  if (!code) {
    setActivationGate(true, "کد را وارد کنید");
    return;
  }
  if (submit) submit.disabled = true;
  try {
    const result = await api("/api/activation", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    state.activated = Boolean(result.activated);
    if (!state.activated) {
      setActivationGate(true, "کد پذیرفته نشد");
      return;
    }
    if (input) input.value = "";
    setActivationGate(false);
    showToast("دسترسی فعال شد");
    await init();
  } catch (e) {
    setActivationGate(true, e.message || "کد پذیرفته نشد");
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function loadSummary() {
  const s = await api(`/api/summary${dateQuery()}`);
  state.lastUpdate = s.last_update || "";
  setText("statContracts", fmtNum(s.underlying_count));
  setText("statBuyOi", fmtNum(s.contract_count));
  setText("statSellOi", fmtNum(s.call_count));
  setText("statNaturalFlow", fmtNum(s.put_count));
  setText("statLegalFlow", fmtNum(s.total_trade_volume));
  setText("lastUpdate", s.last_update
    ? `تاریخ داده: ${state.selectedDate ? fmtDate(state.selectedDate) : "آخرین"} · بروزرسانی: ${fmtDate(s.last_update)}`
    : "بدون داده");
  updateFreshnessBadge();
}

async function loadDates() {
  const input = document.getElementById("dateFilter");
  const options = document.getElementById("availableDates");
  const data = await api("/api/dates");
  state.availableDates = data.items || [];
  state.latestDate = data.latest || "";
  if (!state.selectedDate && data.latest) {
    state.selectedDate = data.latest;
  }
  if (options) {
    options.innerHTML = state.availableDates.length
      ? state.availableDates
          .map((date) => `<option value="${escapeHtml(date)}">${escapeHtml(fmtDate(date))}</option>`)
          .join("")
      : "";
  }
  if (input) input.value = state.selectedDate || "";
  await loadCalendarToday();
  updateDatePickerButton();
  if (!state.calendarView) state.calendarView = selectedJalaliDate();
  if (state.calendarVisible) renderDatePicker();
  syncDateToUrl();
  updateFreshnessBadge();
}

async function loadCalendarToday() {
  try {
    const today = await api("/api/calendar/today");
    const gregorianDate = translateDigits(today?.gregorian_date || "");
    if (validIsoDate(gregorianDate)) {
      state.calendarTodayIso = gregorianDate;
    }
  } catch (e) {
    console.warn("calendar today unavailable", e);
    state.calendarTodayIso = localIsoDate();
  }
}

function updateDatePickerButton() {
  const button = document.getElementById("datePickerButton");
  if (!button) return;
  if (!state.selectedDate) {
    button.textContent = "انتخاب تاریخ";
    return;
  }
  const { jy, jm, jd } = selectedJalaliDate();
  button.textContent = `${jd.toLocaleString("fa-IR")} ${PERSIAN_MONTHS[jm - 1]} ${jy.toLocaleString("fa-IR")}`;
}

async function loadCalendarMonth(year, month) {
  const key = `${year}-${month}`;
  if (state.calendarMonths[key]) return state.calendarMonths[key];
  try {
    const data = await api(`/api/calendar/${year}/${month}/events`);
    state.calendarMonths[key] = data;
    return data;
  } catch (e) {
    console.warn("calendar month unavailable", e);
    return { year, month, days: [], events: [], holidays: [] };
  }
}

function calendarDayMap(monthData) {
  const map = new Map();
  (monthData?.days || []).forEach((day) => {
    map.set(Number(day.day), {
      ...day,
      is_holiday: day.is_holiday === true,
      is_weekend: day.is_weekend === true,
      events: day.events || [],
    });
  });
  (monthData?.events || []).forEach((event) => {
    const day = Number(String(event.jalali_date || "").split("-")[2]);
    if (!day) return;
    const item = map.get(day) || { day, events: [], is_holiday: false, is_weekend: false };
    item.events = item.events || [];
    const eventKey = event.id ?? `${event.jalali_date}:${event.title}`;
    const alreadyExists = item.events.some((existing) => (existing.id ?? `${existing.jalali_date}:${existing.title}`) === eventKey);
    if (!alreadyExists) item.events.push(event);
    item.is_holiday = item.is_holiday || event.is_holiday === true;
    map.set(day, item);
  });
  return map;
}

async function renderDatePicker() {
  const popover = document.getElementById("datePickerPopover");
  const title = document.getElementById("datePickerTitle");
  const grid = document.getElementById("datePickerGrid");
  const foot = document.getElementById("datePickerFoot");
  if (!popover || !title || !grid || !foot) return;
  const view = state.calendarView || selectedJalaliDate();
  const requestId = ++state.calendarRequestId;
  const viewKey = `${view.jy}-${view.jm}`;
  title.textContent = `${PERSIAN_MONTHS[view.jm - 1]} ${view.jy.toLocaleString("fa-IR")}`;
  grid.innerHTML = `<span class="date-picker-loading">در حال دریافت تقویم...</span>`;
  foot.textContent = "روزهای تعطیل با رنگ قرمز مشخص شده‌اند";

  const monthData = await loadCalendarMonth(view.jy, view.jm);
  const currentView = state.calendarView || selectedJalaliDate();
  if (!state.calendarVisible || state.calendarRequestId !== requestId || `${currentView.jy}-${currentView.jm}` !== viewKey) return;
  const dayMap = calendarDayMap(monthData);
  const monthLength = jalaliMonthLength(view.jy, view.jm);
  const firstGregorian = jalaliToGregorian(view.jy, view.jm, 1);
  const firstOffset = (new Date(firstGregorian.gy, firstGregorian.gm - 1, firstGregorian.gd).getDay() + 1) % 7;
  const available = new Set(state.availableDates);
  const selected = selectedJalaliDate();
  const today = calendarTodayIso();
  const cells = [];

  for (let i = 0; i < firstOffset; i += 1) {
    cells.push('<span class="date-picker-empty"></span>');
  }
  for (let day = 1; day <= monthLength; day += 1) {
    const g = jalaliToGregorian(view.jy, view.jm, day);
    const date = isoDate(g.gy, g.gm, g.gd);
    const info = dayMap.get(day) || {};
    const hasSnapshot = available.has(date);
    const isFuture = date > today;
    const isSelected = selected.jy === view.jy && selected.jm === view.jm && selected.jd === day;
    const isWeeklyHoliday = new Date(g.gy, g.gm - 1, g.gd).getDay() === 5;
    const isHoliday = info.is_holiday === true || isWeeklyHoliday;
    const eventTitle = (info.events || []).map((event) => event.title).join("، ");
    const classes = [
      "date-picker-day",
      isHoliday ? "is-holiday" : "",
      hasSnapshot ? "has-data" : "no-data",
      isFuture ? "is-disabled" : "",
      isSelected ? "is-selected" : "",
    ].filter(Boolean).join(" ");
    cells.push(`
      <button type="button" class="${classes}" data-date="${escapeHtml(date)}" ${isFuture ? "disabled" : ""} title="${escapeHtml(eventTitle || (hasSnapshot ? "داده موجود" : isFuture ? "تاریخ آینده" : "بدون داده ذخیره‌شده"))}">
        <strong>${day.toLocaleString("fa-IR")}</strong>
        ${isHoliday ? '<small>تعطیل</small>' : ""}
      </button>
    `);
  }
  grid.innerHTML = cells.join("");
  grid.querySelectorAll(".date-picker-day").forEach((button) => {
    button.addEventListener("click", () => selectCalendarDate(button.dataset.date));
  });
}

function setDatePickerVisible(visible) {
  state.calendarVisible = visible;
  const popover = document.getElementById("datePickerPopover");
  const button = document.getElementById("datePickerButton");
  popover?.classList.toggle("hidden", !visible);
  button?.setAttribute("aria-expanded", String(visible));
  if (visible) {
    state.calendarView = selectedJalaliDate();
    renderDatePicker();
  }
}

function shiftCalendarMonth(delta) {
  const view = state.calendarView || selectedJalaliDate();
  let jy = view.jy;
  let jm = view.jm + delta;
  if (jm < 1) {
    jy -= 1;
    jm = 12;
  } else if (jm > 12) {
    jy += 1;
    jm = 1;
  }
  state.calendarView = { jy, jm, jd: 1 };
  renderDatePicker();
}

async function selectCalendarDate(date) {
  date = translateDigits(date);
  if (!date || date === state.selectedDate) {
    setDatePickerVisible(false);
    return;
  }
  const input = document.getElementById("dateFilter");
  if (input) input.value = date;
  const changed = await changeSelectedDate(date);
  if (changed) {
    setDatePickerVisible(false);
  } else if (input) {
    input.value = state.selectedDate || "";
  }
}

async function changeSelectedDate(date) {
  date = translateDigits(date);
  if (!validIsoDate(date)) {
    const input = document.getElementById("dateFilter");
    if (input) input.value = state.selectedDate || "";
    showToast("تاریخ انتخابی نامعتبر است", "error");
    return false;
  }
  if (state.loading) return false;
  const requestId = ++state.dateChangeRequestId;
  const previous = {
    selectedDate: state.selectedDate,
    selectedRowKey: state.selectedRowKey,
    selectedInsCode: state.selectedInsCode,
    expandedCardKey: state.expandedCardKey,
    underlying: state.underlying,
    analysisVisible: state.analysisVisible,
    items: state.items,
    filtered: state.filtered,
    statusText: document.getElementById("lastUpdate")?.textContent || "",
  };
  state.selectedDate = date;
  state.selectedRowKey = null;
  state.selectedInsCode = null;
  state.clientTypeRequestId += 1;
  state.clientTypeBatchRequestId += 1;
  state.expandedCardKey = null;
  state.underlying = null;
  state.analysisVisible = false;
  state.trendRequestId += 1;
  destroyOiChart();
  destroyTrendChart();
  syncDateToUrl();
  updateDatePickerButton();
  renderDetail(null);
  setStatusText("در حال دریافت داده تاریخ انتخابی...");
  setLoading(true);
  try {
    await loadSummary();
    if (requestId !== state.dateChangeRequestId) return false;
    await reloadActiveData();
    if (requestId !== state.dateChangeRequestId) return false;
    await loadDates();
    if (requestId !== state.dateChangeRequestId) return false;
    return true;
  } catch (e) {
    if (requestId !== state.dateChangeRequestId) return false;
    state.selectedDate = previous.selectedDate;
    state.selectedRowKey = previous.selectedRowKey;
    state.selectedInsCode = previous.selectedInsCode;
    state.expandedCardKey = previous.expandedCardKey;
    state.underlying = previous.underlying;
    state.analysisVisible = previous.analysisVisible;
    state.items = previous.items;
    state.filtered = previous.filtered;
    syncDateToUrl();
    updateDatePickerButton();
    const input = document.getElementById("dateFilter");
    if (input) input.value = previous.selectedDate || "";
    if (previous.statusText) setStatusText(previous.statusText);
    applyFilterAndSort();
    if (previous.selectedInsCode) {
      const selectedRow = state.filtered.find((row) => getRowKey(row) === previous.selectedRowKey) || null;
      renderDetail(selectedRow);
      loadOiChart(previous.selectedInsCode);
    } else {
      renderDetail(null);
    }
    showToast("خطا در تغییر تاریخ", "error");
    setEmptyMessage("خطا در دریافت داده", "اتصال یا تاریخ انتخابی را بررسی کنید و دوباره تلاش کنید");
    console.error(e);
    return false;
  } finally {
    setLoading(false);
  }
}

function currentSearch() {
  return document.getElementById("searchInput")?.value.trim() || "";
}

async function loadUnderlyings(search = "") {
  state.clientTypeBatchRequestId += 1;
  const q = appendQuery({ q: search });
  const data = await api(`/api/underlyings${q}`);
  state.items = data.items || [];
  applyFilterAndSort();
}

async function loadUnderlyingContracts(search = "") {
  const q = appendQuery({ q: search });
  const data = await api(`/api/underlyings/${encodeURIComponent(state.underlyingKey)}/contracts${q}`);
  state.items = data.items || [];
  applyCachedClientTypes(state.items);
  state.underlying = data.underlying || null;
  populateOptionFilters();
  applyFilterAndSort();
  loadClientTypesForRows(state.items);
}

async function reloadActiveData() {
  if (state.view === "underlying") {
    await loadUnderlyingContracts(currentSearch());
  } else {
    await loadUnderlyings(currentSearch());
  }
}

function getRowKey(row) {
  if (!row) return "";
  if (state.view === "underlyings") return row.underlying_key ?? "";
  return row.row_key ?? row.ins_code ?? "";
}

function isUnderlyingAnalysisMode() {
  return state.view === "underlying" && state.analysisVisible;
}

function analysisAudienceItems() {
  return [
    { key: "both", label: "هردو", title: "حقیقی / حقوقی", prefixes: ["natural", "legal"], trendOnly: false },
    { key: "natural", label: "حقیقی", title: "حقیقی", prefixes: ["natural"], trendOnly: false },
    { key: "legal", label: "حقوقی", title: "حقوقی", prefixes: ["legal"], trendOnly: false },
    { key: "naturalTrend", label: "روند ۷ روزه تحلیل حقیقی", title: "روند ۷ روزه تحلیل حقیقی", prefixes: ["natural"], trendOnly: true },
    { key: "legalTrend", label: "روند ۷ روزه تحلیل حقوقی", title: "روند ۷ روزه تحلیل حقوقی", prefixes: ["legal"], trendOnly: true },
  ];
}

function activeAnalysisAudience() {
  return analysisAudienceItems().find((item) => item.key === state.analysisAudience) || analysisAudienceItems()[0];
}

const TREND_METRIC_GROUPS = [
  {
    key: "score",
    label: "امتیاز",
    metrics: [{ key: "score", label: "امتیاز", color: "#38bdf8" }],
  },
  {
    key: "orders",
    label: "Call / Put",
    metrics: [
      { key: "call_buy", label: "Call خرید", color: "#34d399" },
      { key: "call_sell", label: "Call فروش", color: "#f97316" },
      { key: "put_buy", label: "Put خرید", color: "#f472b6" },
      { key: "put_sell", label: "Put فروش", color: "#a78bfa" },
    ],
  },
  {
    key: "structure",
    label: "ترکیب",
    metrics: [
      { key: "itm_volume", label: "ITM", color: "#22d3ee" },
      { key: "otm_volume", label: "OTM", color: "#facc15" },
      { key: "call_volume", label: "حجم Call", color: "#34d399" },
      { key: "put_volume", label: "حجم Put", color: "#fb7185" },
    ],
  },
  {
    key: "oi",
    label: "موقعیت باز",
    metrics: [
      { key: "open_interest_change", label: "تغییر OI", color: "#38bdf8" },
      { key: "open_interest", label: "OI امروز", color: "#a78bfa" },
    ],
  },
];

function activeTrendMetricGroup() {
  return TREND_METRIC_GROUPS.find((group) => group.key === state.trendMetricGroup) || TREND_METRIC_GROUPS[0];
}

function applyLocalFilters(items) {
  if (state.view !== "underlying") return [...items];

  return items.filter((row) => {
    if (state.filters.type !== "all" && row.option_type !== state.filters.type) return false;
    if (state.filters.expiry !== "all" && String(row.end_date) !== state.filters.expiry) return false;
    if (state.filters.moneyness !== "all" && row.moneyness !== state.filters.moneyness) return false;
    if (state.filters.strike !== "all" && String(row.strike_price) !== state.filters.strike) return false;
    return true;
  });
}

function applyFilterAndSort() {
  state.filtered = applyLocalFilters(state.items);
  if (state.sortKey) {
    const key = state.sortKey;
    const dir = state.sortDir;
    state.filtered.sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv), "fa") * dir;
    });
  }
  renderActiveFilters();
  renderMobileContext();
  renderTable();
  renderAnalysis();
}

function updateViewChrome() {
  const config = VIEW_CONFIG[state.view];
  const hasSelectedDetail = Boolean(state.selectedRowKey);
  const detailAvailable = state.view === "underlying" || (state.view === "underlyings" && hasSelectedDetail && isMobileLayout());
  const analysisMode = isUnderlyingAnalysisMode();
  document.body.dataset.view = state.view;
  const searchInput = document.getElementById("searchInput");
  const detailPanel = document.getElementById("detailPanel");
  const analysisButton = document.getElementById("btnAnalysis");
  const mainGrid = document.querySelector(".main-grid");

  if (searchInput) searchInput.placeholder = config.searchPlaceholder;
  document.getElementById("optionFilters")?.classList.toggle("hidden", state.view !== "underlying");
  document.getElementById("btnBack")?.classList.toggle("hidden", state.view !== "underlying");
  detailPanel?.classList.toggle("hidden", !detailAvailable);
  detailPanel?.classList.toggle("sheet-open", detailAvailable && hasSelectedDetail);
  document.getElementById("analysisPanel")?.classList.toggle("hidden", !analysisMode);
  analysisButton?.classList.toggle("active", state.analysisVisible);
  if (analysisButton) analysisButton.textContent = state.analysisVisible ? "بستن آنالیز" : "آنالیز سهم";
  mainGrid?.classList.toggle("hidden", analysisMode);
  mainGrid?.classList.toggle("no-detail", state.view !== "underlying");
  setText("detailTitle", state.view === "underlyings" ? "جزئیات سهم" : "جزئیات قرارداد");
  renderActiveFilters();
  renderMobileContext();
}

function sumField(rows, key) {
  return rows.reduce((total, row) => total + numericValue(row[key]), 0);
}

function minField(rows, key) {
  const values = rows.map((row) => numericValue(row[key])).filter((value) => value > 0);
  return values.length ? Math.min(...values) : null;
}

function maxField(rows, key) {
  const values = rows.map((row) => numericValue(row[key])).filter((value) => value > 0);
  return values.length ? Math.max(...values) : null;
}

function renderMobileContext() {
  const card = document.getElementById("mobileContextCard");
  if (!card) return;
  if (state.view !== "underlying" || (!state.underlying && !state.items.length)) {
    card.classList.add("hidden");
    card.innerHTML = "";
    return;
  }

  const name = state.underlying?.underlying_symbol || state.underlying?.underlying_short_name || "سهم انتخابی";
  const activeFilters = activeFilterItems().length;

  card.innerHTML = `
    <div class="context-head">
      <button type="button" class="context-back" id="contextBackButton">بازگشت</button>
      <div>
        <strong>${escapeHtml(`قراردادهای اختیار ${name}`)}</strong>
        <span>${escapeHtml(`${fmtNum(state.filtered.length)} از ${fmtNum(state.items.length)} قرارداد اختیار`)}</span>
      </div>
      <button type="button" class="context-action" id="contextAnalysisButton">${state.analysisVisible ? "بستن تحلیل" : "تحلیل سهم"}</button>
    </div>
    ${activeFilters ? `<div class="context-foot">${fmtNum(activeFilters)} فیلتر فعال است</div>` : ""}
  `;
  card.classList.remove("hidden");
  document.getElementById("contextBackButton")?.addEventListener("click", goBackToUnderlyings);
  document.getElementById("contextAnalysisButton")?.addEventListener("click", toggleAnalysisMode);
}

function activeFilterItems() {
  if (state.view !== "underlying") return [];
  const items = [];
  if (state.filters.type !== "all") {
    items.push({ key: "type", label: state.filters.type === "call" ? "اختیار خرید" : "اختیار فروش" });
  }
  if (state.filters.expiry !== "all") {
    items.push({ key: "expiry", label: `سررسید ${fmtDate(state.filters.expiry)}` });
  }
  if (state.filters.moneyness !== "all") {
    items.push({ key: "moneyness", label: state.filters.moneyness });
  }
  if (state.filters.strike !== "all") {
    items.push({ key: "strike", label: `اعمال ${fmtNum(state.filters.strike)}` });
  }
  return items;
}

function renderActiveFilters() {
  const wrap = document.getElementById("activeFilters");
  const clearButton = document.getElementById("btnClearFilters");
  if (!wrap || !clearButton) return;
  const items = activeFilterItems();
  wrap.classList.toggle("hidden", !items.length);
  clearButton.classList.toggle("hidden", !items.length);
  clearButton.textContent = items.length ? `پاک کردن ${fmtNum(items.length)} فیلتر` : "پاک کردن فیلترها";
  wrap.innerHTML = items
    .map((item) => `<button type="button" class="filter-chip" data-filter="${escapeHtml(item.key)}"><span>${escapeHtml(item.label)}</span><span aria-hidden="true">×</span></button>`)
    .join("");
  wrap.querySelectorAll(".filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => clearSingleFilter(chip.dataset.filter));
  });
}

function clearSingleFilter(key) {
  if (!key) return;
  if (key === "type") {
    state.filters.type = "all";
    document.querySelectorAll("#typeFilter .segment").forEach((el) => {
      el.classList.toggle("active", el.dataset.type === "all");
    });
  }
  if (key === "expiry") {
    state.filters.expiry = "all";
    const expiryFilter = document.getElementById("expiryFilter");
    if (expiryFilter) expiryFilter.value = "all";
  }
  if (key === "moneyness") {
    state.filters.moneyness = "all";
    const moneynessFilter = document.getElementById("moneynessFilter");
    if (moneynessFilter) moneynessFilter.value = "all";
  }
  if (key === "strike") {
    state.filters.strike = "all";
    const strikeFilter = document.getElementById("strikeFilter");
    if (strikeFilter) strikeFilter.value = "all";
  }
  state.selectedRowKey = null;
  state.selectedInsCode = null;
  state.clientTypeRequestId += 1;
  state.expandedCardKey = null;
  renderDetail(null);
  applyFilterAndSort();
}

function resetFilters() {
  state.filters = {
    type: "all",
    expiry: "all",
    moneyness: "all",
    strike: "all",
  };
  document.querySelectorAll("#typeFilter .segment").forEach((el) => {
    el.classList.toggle("active", el.dataset.type === "all");
  });
  const expiryFilter = document.getElementById("expiryFilter");
  const moneynessFilter = document.getElementById("moneynessFilter");
  const strikeFilter = document.getElementById("strikeFilter");
  if (expiryFilter) expiryFilter.value = "all";
  if (moneynessFilter) moneynessFilter.value = "all";
  if (strikeFilter) strikeFilter.value = "all";
  state.selectedRowKey = null;
  state.selectedInsCode = null;
  state.clientTypeRequestId += 1;
  state.expandedCardKey = null;
  renderDetail(null);
  applyFilterAndSort();
}

function clearSelectionForFilterChange() {
  state.selectedRowKey = null;
  state.selectedInsCode = null;
  state.expandedCardKey = null;
  renderDetail(null);
}

function closeDetailSheet() {
  state.selectedInsCode = null;
  state.selectedRowKey = null;
  state.clientTypeRequestId += 1;
  state.expandedCardKey = null;
  state.oiRequestId += 1;
  renderTable();
  renderDetail(null);
  updateViewChrome();
}

function toggleAnalysisMode() {
  state.analysisVisible = !state.analysisVisible;
  state.selectedInsCode = null;
  state.selectedRowKey = null;
  state.clientTypeRequestId += 1;
  state.expandedCardKey = null;
  destroyOiChart();
  renderDetail(null);
  renderTable();
  renderAnalysis();
}

function renderLoadingSkeleton(on) {
  const list = document.getElementById("mobileCardList");
  if (!list) return;
  if (!on) {
    list.classList.remove("is-loading");
    return;
  }
  list.classList.add("is-loading");
  list.innerHTML = Array.from({ length: 5 }, () => `
    <div class="mobile-data-card skeleton-card" aria-hidden="true">
      <span class="skeleton-line skeleton-title"></span>
      <span class="skeleton-line"></span>
      <span class="skeleton-grid">
        <span></span><span></span><span></span><span></span>
      </span>
    </div>
  `).join("");
}

function renderTable() {
  const config = VIEW_CONFIG[state.view];
  const head = document.getElementById("tableHead");
  const body = document.getElementById("tableBody");
  const empty = document.getElementById("emptyState");
  const wrap = document.getElementById("tableWrap");
  const count = document.getElementById("rowCount");
  const title = document.getElementById("panelTitle");
  const mobileList = document.getElementById("mobileCardList");

  if (!head || !body || !empty || !wrap || !count || !title || !mobileList) return;

  updateViewChrome();
  const underlyingName = state.underlying?.underlying_symbol || state.underlying?.underlying_short_name;
  title.textContent = state.view === "underlying" && underlyingName
    ? `${config.title} ${underlyingName}`
    : config.title;
  count.textContent = `${state.filtered.length} ردیف`;

  if (isUnderlyingAnalysisMode()) {
    wrap.classList.add("hidden");
    empty.classList.add("hidden");
    mobileList.innerHTML = "";
    head.innerHTML = "";
    body.innerHTML = "";
    return;
  }

  if (!state.filtered.length) {
    wrap.classList.add("hidden");
    mobileList.innerHTML = "";
    setEmptyMessage(
      state.items.length ? "نتیجه‌ای با این فیلترها پیدا نشد" : "داده‌ای موجود نیست",
      state.items.length ? "فیلترها را پاک کنید یا عبارت جستجو را تغییر دهید" : "ابتدا pipeline را اجرا کنید یا دکمه به‌روزرسانی را بزنید"
    );
    empty.classList.remove("hidden");
    head.innerHTML = "";
    body.innerHTML = "";
    return;
  }

  wrap.classList.remove("hidden");
  empty.classList.add("hidden");

  head.innerHTML = `<tr>${config.columns
    .map(
      (c) =>
        `<th data-key="${escapeHtml(c.key)}" class="${state.sortKey === c.key ? "sorted" : ""}">${escapeHtml(c.label)}</th>`
    )
    .join("")}</tr>`;

  body.innerHTML = state.filtered
    .map((row, index) => {
      const selected = getRowKey(row) === state.selectedRowKey ? "selected" : "";
      const cells = config.columns
        .map((c) => {
          const val = row[c.key];
          let cls = "";
          if (c.fmt === "flow" && val != null) {
            cls = val > 0 ? "cell-positive" : val < 0 ? "cell-negative" : "";
          }
          if (c.fmt === "optionType") {
            cls = `type-badge ${optionTypeClass(row.option_type)}`;
          }
          return `<td><span class="${cls}">${escapeHtml(formatCell(c, val))}</span></td>`;
        })
        .join("");
      return `<tr data-index="${index}" class="${selected}" tabindex="0" role="button">${cells}</tr>`;
    })
    .join("");

  head.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.sortKey === key) state.sortDir *= -1;
      else {
        state.sortKey = key;
        state.sortDir = 1;
      }
      applyFilterAndSort();
    });
  });

  body.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      selectRowByIndex(Number(tr.dataset.index));
    });
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectRowByIndex(Number(tr.dataset.index));
      }
    });
  });

  renderMobileCards();
}

function mobileMetric(label, value, kind = "text", extraClass = "") {
  return `
    <span class="mobile-metric ${escapeHtml(extraClass)}">
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(formatMobileValue(value, kind))}</strong>
    </span>
  `;
}

function formatMobileValue(value, kind = "text") {
  if (kind === "num") return fmtNum(value);
  if (kind === "compact") return fmtCompactNum(value);
  if (kind === "date") return fmtDate(value);
  if (kind === "optionType") return optionTypeLabel(value);
  if (kind === "flow") return fmtFlow(value);
  return value ?? "—";
}

function activityLabel(row) {
  const volume = numericValue(row.trade_volume);
  const count = numericValue(row.contract_count);
  if (!count) return "بدون قرارداد";
  if (volume >= 1000000) return "بسیار فعال";
  if (volume >= 100000) return "فعال";
  if (volume > 0) return "کم‌معامله";
  return "بدون معامله";
}

function optionSignalLabel(row) {
  const flow = numericValue(row.natural_money_flow);
  const oi = oiChange(row);
  if (flow > 0 && oi > 0) return "جریان مثبت و OI افزایشی";
  if (flow < 0 && oi < 0) return "جریان منفی و OI کاهشی";
  if (flow > 0) return "جریان حقیقی مثبت";
  if (flow < 0) return "جریان حقیقی منفی";
  if (oi > 0) return "OI افزایشی";
  if (oi < 0) return "OI کاهشی";
  return "متعادل";
}

function oiChange(row) {
  const current = openInterestValue(row);
  if (current == null || row.yesterday_open_positions == null) return null;
  return numericValue(current) - numericValue(row.yesterday_open_positions);
}

function rowSignalClass(row) {
  const flow = numericValue(row.natural_money_flow);
  const oi = oiChange(row);
  if (flow > 0 || oi > 0) return "signal-positive";
  if (flow < 0 || oi < 0) return "signal-negative";
  return "signal-neutral";
}

function renderMobileCards() {
  const list = document.getElementById("mobileCardList");
  if (!list) return;
  const listTitle = state.view === "underlying"
    ? `<div class="mobile-list-title"><span>قراردادها</span><strong>${fmtNum(state.filtered.length)} مورد</strong></div>`
    : "";

  list.innerHTML = listTitle + state.filtered
    .map((row, index) => {
      const isUnderlying = state.view === "underlyings";
      const rowKey = getRowKey(row);
      const isExpanded = isUnderlying ? rowKey === state.expandedCardKey : rowKey === state.selectedRowKey;
      const selected = isExpanded ? " selected is-expanded" : "";
      const typeClass = optionTypeClass(row.option_type);
      const signalClass = rowSignalClass(row);
      const title = isUnderlying
        ? row.underlying_symbol || row.underlying_short_name || "—"
        : row.symbol || row.short_name || "—";
      const subtitle = isUnderlying
        ? row.underlying_short_name || "سهم پایه"
        : `${optionTypeLabel(row.option_type)} · سررسید ${fmtDate(row.end_date)}`;
      const badge = isUnderlying
        ? `${fmtNum(row.contract_count)} قرارداد`
        : row.moneyness || "—";
      const metrics = isUnderlying
        ? [
            mobileMetric("اختیار خرید", row.call_count, "num", "metric-call"),
            mobileMetric("اختیار فروش", row.put_count, "num", "metric-put"),
            mobileMetric("سررسید", row.nearest_end_date, "date"),
            mobileMetric("حجم", row.trade_volume, "compact", "metric-volume"),
          ].join("")
        : [
            mobileMetric("اعمال", row.strike_price, "num"),
            mobileMetric("آخرین", row.last_price, "num"),
            mobileMetric("حجم", row.trade_volume, "compact", "metric-volume"),
            mobileMetric("OI", row.buy_open_positions, "compact"),
          ].join("");
      const note = isUnderlying ? activityLabel(row) : optionSignalLabel(row);
      const action = isUnderlying
        ? `<button type="button" class="mobile-card-action" data-action="open-underlying" data-index="${index}">مشاهده قراردادها</button>`
        : "";
      return `
        <article class="mobile-data-card ${isUnderlying ? "mobile-underlying-card" : `mobile-option-card ${signalClass}`}${selected}" data-index="${index}" tabindex="0" role="button" aria-expanded="${isExpanded ? "true" : "false"}">
          <span class="mobile-card-head">
            <span>
              <strong>${escapeHtml(title)}</strong>
              <small>${escapeHtml(subtitle)}</small>
            </span>
            <span class="mobile-card-badge ${isUnderlying ? "" : typeClass}">${escapeHtml(badge)}</span>
            <span class="mobile-card-indicator" aria-hidden="true"></span>
          </span>
          <span class="mobile-card-details" aria-hidden="${isExpanded ? "false" : "true"}">
            <span class="mobile-card-metrics">${metrics}</span>
            <span class="mobile-card-note">${escapeHtml(note)}</span>
            ${action}
          </span>
        </article>
      `;
    })
    .join("");

  list.querySelectorAll(".mobile-data-card").forEach((card) => {
    card.addEventListener("click", (event) => {
      const action = event.target.closest("[data-action]");
      if (action?.dataset.action === "open-underlying") {
        event.stopPropagation();
        openUnderlyingPage(state.filtered[Number(action.dataset.index)]);
        return;
      }
      selectMobileCardByIndex(Number(card.dataset.index));
    });
    card.addEventListener("keydown", (event) => {
      if (event.target.closest("[data-action]")) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      selectMobileCardByIndex(Number(card.dataset.index));
    });
  });
}

function selectMobileCardByIndex(index) {
  const row = state.filtered[index];
  if (!row) return;

  if (state.view === "underlyings") {
    const rowKey = getRowKey(row);
    state.expandedCardKey = state.expandedCardKey === rowKey ? null : rowKey;
    renderMobileCards();
    return;
  }

  selectRowByIndex(index);
}

function selectRowByIndex(index) {
  const row = state.filtered[index];
  if (!row) return;

  if (state.view === "underlyings") {
    openUnderlyingPage(row);
    return;
  }

  state.selectedRowKey = getRowKey(row);
  state.selectedInsCode = row?.ins_code ?? null;
  renderTable();
  renderDetail(row);
  loadClientTypeForSelected(row);
  updateViewChrome();
  if (state.selectedInsCode) {
    loadOiChart(state.selectedInsCode);
  } else {
    document.getElementById("chartBlock")?.classList.add("hidden");
  }
}

async function loadClientTypeForSelected(row) {
  const insCode = row?.ins_code;
  if (!insCode) return;
  const requestId = ++state.clientTypeRequestId;
  try {
    const data = await api(`/api/client-type/${encodeURIComponent(insCode)}`);
    if (requestId !== state.clientTypeRequestId || String(insCode) !== String(state.selectedInsCode)) return;
    const item = data.item || {};
    state.clientTypeByInsCode[String(insCode)] = item;
    mergeClientTypeIntoRow(row, item);
    const rowKey = getRowKey(row);
    state.items.forEach((itemRow) => {
      if (getRowKey(itemRow) === rowKey) mergeClientTypeIntoRow(itemRow, item);
    });
    state.filtered.forEach((itemRow) => {
      if (getRowKey(itemRow) === rowKey) mergeClientTypeIntoRow(itemRow, item);
    });
    renderTable();
    renderDetail(row);
    renderAnalysis();
  } catch (e) {
    if (requestId === state.clientTypeRequestId && String(insCode) === String(state.selectedInsCode)) {
      console.error(e);
    }
  }
}

async function loadClientTypesForRows(rows) {
  const insCodes = [...new Set((rows || []).map((row) => row?.ins_code).filter(Boolean).map(String))];
  if (!insCodes.length) return;
  const requestId = ++state.clientTypeBatchRequestId;
  try {
    const data = await api("/api/client-type", {
      method: "POST",
      body: JSON.stringify({ ins_codes: insCodes }),
    });
    if (requestId !== state.clientTypeBatchRequestId) return;
    (data.items || []).forEach((clientType) => {
      const insCode = String(clientType?.ins_code || "");
      if (!insCode) return;
      state.clientTypeByInsCode[insCode] = clientType;
      state.items.forEach((row) => {
        if (String(row?.ins_code) === insCode) mergeClientTypeIntoRow(row, clientType);
      });
    });
    applyFilterAndSort();
    if (state.selectedRowKey) {
      const selectedRow = state.filtered.find((row) => getRowKey(row) === state.selectedRowKey) || null;
      renderDetail(selectedRow);
    }
  } catch (e) {
    if (requestId === state.clientTypeBatchRequestId) console.error(e);
  }
}

function applyCachedClientTypes(rows) {
  (rows || []).forEach((row) => {
    const clientType = state.clientTypeByInsCode[String(row?.ins_code || "")];
    if (clientType) mergeClientTypeIntoRow(row, clientType);
  });
}

function mergeClientTypeIntoRow(row, clientType) {
  if (!row || !clientType) return row;
  [
    "rec_date",
    "natural_buy_volume",
    "natural_buy_value",
    "natural_buy_count",
    "natural_sell_volume",
    "natural_sell_value",
    "natural_sell_count",
    "legal_buy_volume",
    "legal_buy_value",
    "legal_buy_count",
    "legal_sell_volume",
    "legal_sell_value",
    "legal_sell_count",
    "natural_money_flow",
    "legal_money_flow",
  ].forEach((key) => {
    if (clientType[key] !== undefined) row[key] = clientType[key];
  });
  return row;
}

function renderDetail(row) {
  const container = document.getElementById("detailContent");
  if (!container) return;
  if (!row) {
    container.innerHTML = '<p class="detail-placeholder">یک ردیف از جدول را انتخاب کنید</p>';
    destroyOiChart();
    return;
  }

  if (state.view === "underlyings") {
    renderUnderlyingDetail(row);
    return;
  }

  const hero = `
    <section class="detail-hero">
      <div>
        <strong>${escapeHtml(row.symbol || "—")}</strong>
        <span>${escapeHtml(row.short_name || row.long_name || "قرارداد اختیار")}</span>
      </div>
      <div class="detail-hero-badges">
        <span class="type-badge ${optionTypeClass(row.option_type)}">${escapeHtml(optionTypeLabel(row.option_type))}</span>
        <span class="detail-status">${escapeHtml(row.moneyness || "—")}</span>
      </div>
      <div class="detail-hero-metrics">
        ${mobileMetric("اعمال", row.strike_price, "num")}
        ${mobileMetric("سررسید", row.end_date, "date")}
        ${mobileMetric("آخرین", row.last_price, "num")}
        ${mobileMetric("OI", row.buy_open_positions, "num")}
      </div>
    </section>
  `;

  const summaryRows = [
    ["total_buy_volume", sumPresentValues(row, ["natural_buy_volume", "legal_buy_volume"])],
    ["total_sell_volume", sumPresentValues(row, ["natural_sell_volume", "legal_sell_volume"])],
    ["open_interest_positions", openInterestValue(row)],
    ["open_interest_change", oiChange(row)],
  ]
    .filter(([, value]) => value != null)
    .map(([key, value]) => renderDetailRow(key, value));

  const sections = [
    ["اطلاعات قرارداد", ["option_type", "symbol", "short_name", "long_name", "ins_code", "strike_price", "end_date", "contract_size", "moneyness", "intrinsic_value"]],
    ["معاملات", ["last_price", "closing_price", "price_change", "trade_volume", "trade_value"]],
    ["موقعیت", ["buy_open_positions", "sell_open_positions", "yesterday_open_positions"]],
    ["جریان پول", ["natural_money_flow", "legal_money_flow"]],
    ["حقیقی", ["natural_buy_count", "natural_buy_volume", "natural_buy_value", "natural_sell_count", "natural_sell_volume", "natural_sell_value"]],
    ["حقوقی", ["legal_buy_count", "legal_buy_volume", "legal_buy_value", "legal_sell_count", "legal_sell_volume", "legal_sell_value"]],
  ];

  let html = hero;
  if (summaryRows.length) {
    html += `<div class="detail-section-title">خلاصه حجم و موقعیت</div><div class="detail-grid">${summaryRows.join("")}</div>`;
  }
  for (const [title, keys] of sections) {
    const rows = keys
      .filter((k) => row[k] != null)
      .map((k) => renderDetailRow(k, row[k]));
    if (rows.length) {
      html += `<div class="detail-section-title">${escapeHtml(title)}</div><div class="detail-grid">${rows.join("")}</div>`;
    }
  }
  container.innerHTML = html || '<p class="detail-placeholder">جزئیات موجود نیست</p>';
}

function renderDetailRow(key, value) {
  return `<div class="detail-row"><span>${escapeHtml(DETAIL_LABELS[key] || key)}</span><span>${escapeHtml(formatDetailValue(key, value))}</span></div>`;
}

function renderUnderlyingDetail(row) {
  const container = document.getElementById("detailContent");
  destroyOiChart();
  const name = row.underlying_symbol || row.underlying_short_name || "—";
  const sections = [
    ["مشخصات سهم", ["underlying_symbol", "underlying_short_name", "underlying_ins_code", "sector"]],
    ["قراردادها", ["contract_count", "call_count", "put_count", "nearest_end_date"]],
    ["قیمت اعمال", ["min_strike_price", "max_strike_price"]],
    ["معاملات", ["trade_volume", "trade_value"]],
  ];
  let html = `
    <section class="detail-hero">
      <div>
        <strong>${escapeHtml(name)}</strong>
        <span>${escapeHtml(row.underlying_short_name || "سهم پایه")}</span>
      </div>
      <div class="detail-hero-metrics">
        ${mobileMetric("قرارداد", row.contract_count, "num")}
        ${mobileMetric("اختیار خرید", row.call_count, "num")}
        ${mobileMetric("اختیار فروش", row.put_count, "num")}
        ${mobileMetric("حجم", row.trade_volume, "num")}
      </div>
      <button type="button" class="btn btn-primary detail-contracts-button" id="openUnderlyingContracts">
        جزئیات قراردادها
      </button>
    </section>`;

  for (const [title, keys] of sections) {
    const rows = keys
      .filter((k) => row[k] != null)
      .map(
        (k) =>
          `<div class="detail-row"><span>${escapeHtml(DETAIL_LABELS[k] || k)}</span><span>${escapeHtml(formatDetailValue(k, row[k]))}</span></div>`
      );
    if (rows.length) {
      html += `<div class="detail-section-title">${escapeHtml(title)}</div><div class="detail-grid">${rows.join("")}</div>`;
    }
  }
  container.innerHTML = html;
  document.getElementById("openUnderlyingContracts")?.addEventListener("click", () => openUnderlyingPage(row));
}

function formatDetailValue(key, val) {
  if (Array.isArray(val)) return val.length ? val.join("، ") : "—";
  if (key === "option_type") return optionTypeLabel(val);
  if (key === "confidence") return fmtPct(val, 1);
  if (key.includes("share")) return fmtPct(val);
  if (key.includes("ratio")) return fmtRatio(val);
  if (key.includes("flow")) return fmtFlow(val);
  if (key.includes("count") || key.includes("value") || key.includes("volume") || key.includes("price") || key.includes("positions") || key.includes("interest")) {
    if (typeof val === "number") return fmtNum(val);
  }
  if (key.includes("date") || key === "end_date" || key === "rec_date") return fmtDate(val);
  return val ?? "—";
}

async function loadOiChart(insCode) {
  const block = document.getElementById("chartBlock");
  const ctx = document.getElementById("oiChart");
  if (!block || !ctx) return;
  const requestId = ++state.oiRequestId;
  try {
    const data = await api(`/api/open-interest/${insCode}${dateQuery()}`);
    if (requestId !== state.oiRequestId || String(insCode) !== String(state.selectedInsCode)) return;
    const history = data.history || [];
    if (!history.length) {
      destroyOiChart();
      return;
    }
    block.classList.remove("hidden");
    const labels = history.map((h) => fmtDate(h.fetched_at));
    const buy = history.map((h) => h.buy_open_positions);
    const sell = history.map((h) => h.sell_open_positions);
    const palette = chartPalette();

    if (state.oiChart) state.oiChart.destroy();
    state.oiChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "موقعیت باز",
            data: buy,
            borderColor: palette.accent,
            backgroundColor: currentTheme() === "dark" ? "rgba(32, 183, 174, 0.12)" : "rgba(15, 139, 141, 0.1)",
            tension: 0.3,
            fill: true,
          },
          {
            label: "موقعیت فروش",
            data: sell,
            borderColor: palette.purple,
            backgroundColor: currentTheme() === "dark" ? "rgba(181, 167, 255, 0.1)" : "rgba(109, 91, 208, 0.1)",
            tension: 0.3,
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: palette.muted, font: { family: "Vazirmatn" } } },
        },
        scales: {
          x: { ticks: { color: palette.muted, maxTicksLimit: 6 }, grid: { color: palette.grid } },
          y: { ticks: { color: palette.muted }, grid: { color: palette.grid } },
        },
      },
    });
  } catch {
    if (requestId === state.oiRequestId) destroyOiChart();
  }
}

function destroyOiChart() {
  state.oiRequestId += 1;
  if (state.oiChart) {
    state.oiChart.destroy();
    state.oiChart = null;
  }
  document.getElementById("chartBlock")?.classList.add("hidden");
}

function populateOptionFilters() {
  if (state.view !== "underlying") return;
  populateSelectFilter(
    "expiryFilter",
    "همه سررسیدها",
    state.filters.expiry,
    [...new Set(state.items.map((row) => row.end_date).filter(Boolean).map(String))].sort(),
    fmtDate
  );
  state.filters.expiry = document.getElementById("expiryFilter")?.value || "all";

  populateSelectFilter(
    "strikeFilter",
    "همه اعمال‌ها",
    state.filters.strike,
    [...new Set(state.items.map((row) => row.strike_price).filter((value) => value != null).map(String))]
      .sort((a, b) => Number(a) - Number(b)),
    fmtNum
  );
  state.filters.strike = document.getElementById("strikeFilter")?.value || "all";
}

function populateSelectFilter(id, allLabel, current, values, formatter) {
  const select = document.getElementById(id);
  if (!select) return;
  select.innerHTML = `<option value="all">${escapeHtml(allLabel)}</option>` +
    values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(formatter(value))}</option>`).join("");
  select.value = values.includes(current) ? current : "all";
}

const ANALYSIS_SUM_FIELDS = [
  "natural_buy_count",
  "natural_buy_volume",
  "natural_buy_value",
  "natural_sell_count",
  "natural_sell_volume",
  "natural_sell_value",
  "legal_buy_count",
  "legal_buy_volume",
  "legal_buy_value",
  "legal_sell_count",
  "legal_sell_volume",
  "legal_sell_value",
  "buy_open_positions",
  "sell_open_positions",
  "yesterday_open_positions",
];

function emptyAnalysisBucket() {
  return ANALYSIS_SUM_FIELDS.reduce(
    (bucket, field) => {
      bucket[field] = 0;
      bucket._presentFields[field] = 0;
      return bucket;
    },
    { contract_count: 0, open_interest_positions: 0, _presentFields: { open_interest_positions: 0 } }
  );
}

function emptyAnalysisModel() {
  return {
    call: { ITM: emptyAnalysisBucket(), OTM: emptyAnalysisBucket() },
    put: { ITM: emptyAnalysisBucket(), OTM: emptyAnalysisBucket() },
  };
}

function numericValue(value) {
  return asFiniteNumber(value) ?? 0;
}

function openInterestValue(row) {
  const buy = asFiniteNumber(row.buy_open_positions);
  const sell = asFiniteNumber(row.sell_open_positions);
  if (buy == null) return sell;
  if (sell == null) return buy;
  return Math.max(buy, sell);
}

function buildAnalysisModel(rows) {
  const model = emptyAnalysisModel();
  rows.forEach((row) => {
    const optionType = row.option_type;
    const moneyness = row.moneyness;
    if (!model[optionType] || !model[optionType][moneyness]) return;
    const bucket = model[optionType][moneyness];
    bucket.contract_count += 1;
    const openInterest = asFiniteNumber(openInterestValue(row));
    if (openInterest != null) {
      bucket.open_interest_positions += openInterest;
      bucket._presentFields.open_interest_positions += 1;
    }
    ANALYSIS_SUM_FIELDS.forEach((field) => {
      const value = asFiniteNumber(row[field]);
      if (value == null) return;
      bucket[field] += value;
      bucket._presentFields[field] += 1;
    });
  });
  return model;
}

function sumRows(rows, getter) {
  return rows.reduce((total, row) => total + numericValue(getter(row)), 0);
}

function sumPresentValues(row, keys) {
  const values = keys.map((key) => asFiniteNumber(row[key])).filter((value) => value != null);
  return values.length ? values.reduce((total, value) => total + value, 0) : null;
}

function rowBuyVolume(row) {
  return numericValue(row.natural_buy_volume) + numericValue(row.legal_buy_volume);
}

function rowSellVolume(row) {
  return numericValue(row.natural_sell_volume) + numericValue(row.legal_sell_volume);
}

function rowParticipantBuyVolume(row, prefix) {
  return numericValue(row[`${prefix}_buy_volume`]);
}

function rowParticipantSellVolume(row, prefix) {
  return numericValue(row[`${prefix}_sell_volume`]);
}

function rowParticipantVolume(row, prefix) {
  return rowParticipantBuyVolume(row, prefix) + rowParticipantSellVolume(row, prefix);
}

function rowTradeVolume(row) {
  const tradeVolume = asFiniteNumber(row.trade_volume);
  return tradeVolume ?? Math.max(rowBuyVolume(row), rowSellVolume(row));
}

function ratioLabel(num, den) {
  if (!den) return num ? "∞" : "—";
  return (num / den).toLocaleString("fa-IR", { maximumFractionDigits: 2 });
}

function analysisMetricValue(value) {
  if (value == null) return "—";
  return typeof value === "number" ? fmtNum(value) : value;
}

function openInterestSnapshot(rows) {
  const hasCurrent = rows.some((row) => openInterestValue(row) != null);
  const hasYesterday = rows.some((row) => row.yesterday_open_positions != null);
  const current = hasCurrent ? sumRows(rows, (row) => openInterestValue(row)) : null;
  const yesterday = hasYesterday ? sumRows(rows, (row) => row.yesterday_open_positions) : null;
  return {
    hasCurrent,
    hasYesterday,
    hasChange: hasCurrent && hasYesterday,
    current,
    yesterday,
    change: hasCurrent && hasYesterday ? current - yesterday : null,
  };
}

function buildFourStepConclusion(rows, prefix, personLabel, personClass) {
  const callRows = rows.filter((row) => row.option_type === "call");
  const putRows = rows.filter((row) => row.option_type === "put");
  const callItmRows = callRows.filter((row) => row.moneyness === "ITM");
  const callOtmRows = callRows.filter((row) => row.moneyness === "OTM");
  const putItmRows = putRows.filter((row) => row.moneyness === "ITM");
  const putOtmRows = putRows.filter((row) => row.moneyness === "OTM");

  const callBuy = sumRows(callRows, (row) => rowParticipantBuyVolume(row, prefix));
  const callSell = sumRows(callRows, (row) => rowParticipantSellVolume(row, prefix));
  const putBuy = sumRows(putRows, (row) => rowParticipantBuyVolume(row, prefix));
  const putSell = sumRows(putRows, (row) => rowParticipantSellVolume(row, prefix));
  const callItmVolume = sumRows(callItmRows, (row) => rowParticipantVolume(row, prefix));
  const callOtmVolume = sumRows(callOtmRows, (row) => rowParticipantVolume(row, prefix));
  const putItmVolume = sumRows(putItmRows, (row) => rowParticipantVolume(row, prefix));
  const putOtmVolume = sumRows(putOtmRows, (row) => rowParticipantVolume(row, prefix));
  const callVolume = sumRows(callRows, (row) => rowParticipantVolume(row, prefix));
  const putVolume = sumRows(putRows, (row) => rowParticipantVolume(row, prefix));
  const callOi = openInterestSnapshot(callRows);
  const putOi = openInterestSnapshot(putRows);
  const hasOiChange = callOi.hasChange || putOi.hasChange;

  const callBuyDominates = callBuy > callSell;
  const callSellDominates = callSell > callBuy;
  const putSellDominates = putSell > putBuy;
  const putBuyDominates = putBuy > putSell;
  const step1Score =
    (callBuyDominates ? 1 : callSellDominates ? -1 : 0) +
    (putSellDominates ? 1 : putBuyDominates ? -1 : 0);
  const callOtmDominates = callOtmVolume > callItmVolume;
  const callItmDominates = callItmVolume > callOtmVolume;
  const putOtmDominates = putOtmVolume > putItmVolume;
  const putItmDominates = putItmVolume > putOtmVolume;
  const step2Score =
    (callOtmDominates ? 1 : callItmDominates ? 0.5 : 0) +
    (putOtmDominates ? -1 : 0);
  const step2Bullish = step2Score > 0;
  const step2Weak = step2Score < 0;
  const step2Cautious = !step2Bullish && !step2Weak && (callItmDominates || putItmDominates);
  const step3Bullish = callVolume > putVolume;
  const step4CallConfirm = callOi.change != null && callOi.change > 0;
  const step4CallWeak = callOi.change != null && callOi.change < 0;
  const step4PutConfirm = putOi.change != null && putOi.change < 0;
  const step4PutWeak = putOi.change != null && putOi.change > 0;
  const step4Score =
    (step4CallConfirm ? 1 : step4CallWeak ? -1 : 0) +
    (step4PutConfirm ? 1 : step4PutWeak ? -1 : 0);
  const step4Confirm = step4Score > 0;
  const step4Weak = step4Score < 0;

  const score =
    step1Score +
    step2Score +
    (step3Bullish ? 1 : callVolume < putVolume ? -1 : 0) +
    step4Score;

  let finalLabel = "خنثی";
  let finalClass = "neutral";
  if (score >= 5) {
    finalLabel = "صعودی قوی";
    finalClass = "bullish";
  } else if (score >= 3) {
    finalLabel = "صعودی محتاط";
    finalClass = "cautious";
  } else if (score <= -1) {
    finalLabel = "ضعیف";
    finalClass = "weak";
  }

  return {
    personLabel,
    personClass,
    finalLabel,
    finalClass,
    score,
    steps: [
      {
        kicker: "جریان سفارش",
        title: "حجم خرید و فروش",
        label: [
          callBuyDominates
            ? "Call: خرید بیشتر؛ صعودی"
            : callSellDominates
              ? "Call: فروش بیشتر؛ ضعیف"
              : "Call: متعادل",
          putSellDominates
            ? "Put: فروش بیشتر؛ صعودی"
            : putBuyDominates
              ? "Put: خرید بیشتر؛ ضعیف"
              : "Put: متعادل",
        ].filter(Boolean).join("، ") || "بدون برتری روشن",
        className: step1Score > 0 ? "bullish" : step1Score < 0 ? "weak" : "neutral",
        signals: [
          {
            label: callBuyDominates ? "Call صعودی" : callSellDominates ? "Call ضعیف" : "Call متعادل",
            className: callBuyDominates ? "bullish" : callSellDominates ? "weak" : "neutral",
          },
          {
            label: putSellDominates ? "Put صعودی" : putBuyDominates ? "Put ضعیف" : "Put متعادل",
            className: putSellDominates ? "bullish" : putBuyDominates ? "weak" : "neutral",
          },
        ],
        metrics: [
          ["Call خرید", callBuy],
          ["Call فروش", callSell],
          ["Put خرید", putBuy],
          ["Put فروش", putSell],
        ],
      },
      {
        kicker: "محدوده قیمت اعمال",
        title: "ITM و OTM",
        label: step2Bullish ? "Call مثبت‌تر" : step2Weak ? "Put ضعیف‌تر" : step2Cautious ? "محتاط" : "متعادل",
        className: step2Bullish ? "bullish" : step2Weak ? "weak" : step2Cautious ? "cautious" : "neutral",
        signals: [
          {
            label: callOtmDominates ? "Call OTM غالب" : callItmDominates ? "Call ITM غالب" : "Call متعادل",
            className: callOtmDominates ? "bullish" : callItmDominates ? "cautious" : "neutral",
          },
          {
            label: putOtmDominates ? "Put OTM غالب" : putItmDominates ? "Put ITM غالب" : "Put متعادل",
            className: putOtmDominates ? "weak" : putItmDominates ? "cautious" : "neutral",
          },
        ],
        metrics: [
          ["Call ITM", callItmVolume],
          ["Call OTM", callOtmVolume],
          ["Put ITM", putItmVolume],
          ["Put OTM", putOtmVolume],
        ],
      },
      {
        kicker: "ترکیب قراردادها",
        title: "نسبت Call به Put",
        label: step3Bullish ? "Call غالب" : callVolume < putVolume ? "Put غالب" : "متعادل",
        className: step3Bullish ? "bullish" : callVolume < putVolume ? "weak" : "neutral",
        metrics: [
          ["Call", callVolume],
          ["Put", putVolume],
          ["نسبت", ratioLabel(callVolume, putVolume)],
        ],
      },
      {
        kicker: "تأیید موقعیت",
        title: "Open Interest",
        label: !hasOiChange ? "داده تغییر موجود نیست" : step4Confirm ? "تأییدکننده" : step4Weak ? "تضعیف‌کننده" : "بدون تغییر",
        className: step4Confirm ? "bullish" : step4Weak ? "weak" : "neutral",
        signals: [
          {
            label: !callOi.hasChange ? "Call بدون داده تغییر" : step4CallConfirm ? "Call افزایشی" : step4CallWeak ? "Call کاهشی" : "Call بدون تغییر",
            className: step4CallConfirm ? "bullish" : step4CallWeak ? "weak" : "neutral",
          },
          {
            label: !putOi.hasChange ? "Put بدون داده تغییر" : step4PutConfirm ? "Put کاهشی" : step4PutWeak ? "Put افزایشی" : "Put بدون تغییر",
            className: step4PutConfirm ? "bullish" : step4PutWeak ? "weak" : "neutral",
          },
        ],
        groups: [
          {
            title: "Call",
            label: "اختیار خرید",
            className: step4CallConfirm ? "bullish" : step4CallWeak ? "weak" : "neutral",
            metrics: [
              ["امروز", callOi.current],
              ["دیروز", callOi.yesterday],
              ["تغییر", callOi.change],
            ],
          },
          {
            title: "Put",
            label: "اختیار فروش",
            className: step4PutConfirm ? "bullish" : step4PutWeak ? "weak" : "neutral",
            metrics: [
              ["امروز", putOi.current],
              ["دیروز", putOi.yesterday],
              ["تغییر", putOi.change],
            ],
          },
        ],
      },
    ],
  };
}

function renderAnalysisStepMetrics(step) {
  if (step.groups) {
    return `
      <div class="analysis-step-groups">
        ${step.groups
          .map(
            (group) => `
              <section class="analysis-step-group analysis-step-group-${group.className}">
                <div class="analysis-step-group-head">
                  <strong>${escapeHtml(group.title)}</strong>
                  <span>${escapeHtml(group.label)}</span>
                </div>
                <div class="analysis-step-group-metrics">
                  ${group.metrics
                    .map(
                      ([label, value]) => `
                        <div class="${label === "تغییر" ? "analysis-step-group-change" : ""}">
                          <span>${escapeHtml(label)}</span>
                          <strong>${escapeHtml(analysisMetricValue(value))}</strong>
                        </div>`
                    )
                    .join("")}
                </div>
              </section>`
          )
          .join("")}
      </div>`;
  }
  return `
    <div class="analysis-step-metrics">
      ${step.metrics
        .map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(analysisMetricValue(value))}</strong></div>`)
        .join("")}
    </div>`;
}

function renderFourStepConclusion(rows) {
  const audience = activeAnalysisAudience();
  const people = [
    { key: "natural", prefix: "natural", label: "حقیقی", className: "analysis-person-natural" },
    { key: "legal", prefix: "legal", label: "حقوقی", className: "analysis-person-legal" },
  ];
  const conclusions = people
    .filter((person) => audience.prefixes.includes(person.key))
    .map((person) => buildFourStepConclusion(rows, person.prefix, person.label, person.className));
  return `
    <section class="analysis-conclusion">
      <div class="analysis-conclusion-head">
        <span>نتیجه‌گیری چهارگام</span>
        <strong>${escapeHtml(audience.key === "both" ? "تفکیک حقیقی / حقوقی" : audience.label)}</strong>
      </div>
      <div class="analysis-conclusion-groups">
        ${conclusions
          .map(
            (conclusion) => `
              <section class="analysis-conclusion-person analysis-conclusion-${conclusion.finalClass}">
                <div class="analysis-conclusion-person-head">
                  <span class="${conclusion.personClass}">${conclusion.personLabel}</span>
                  <strong>${conclusion.finalLabel}</strong>
                </div>
                <div class="analysis-step-grid">
	                  ${conclusion.steps
	                    .map(
	                      (step) => `
	                        <article class="analysis-step analysis-step-${step.className}">
	                          <div class="analysis-step-title">
	                            <span>${escapeHtml(step.kicker)}</span>
	                            <strong>${escapeHtml(step.title)}</strong>
	                          </div>
	                          ${step.signals
                              ? `<div class="analysis-step-labels">
                                  ${step.signals
                                    .map(
                                      (signal) => `
                                        <span class="analysis-step-label analysis-step-label-${signal.className}">
                                          ${escapeHtml(signal.label)}
                                        </span>`
                                    )
                                    .join("")}
                                </div>`
                              : `<div class="analysis-step-label">${escapeHtml(step.label)}</div>`}
	                          ${renderAnalysisStepMetrics(step)}
	                        </article>`
	                    )
	                    .join("")}
                </div>
              </section>`
          )
          .join("")}
      </div>
    </section>`;
}

function renderTrendShell() {
  const audience = activeAnalysisAudience();
  return `
    <section class="analysis-conclusion trend-analysis" id="trendAnalysis">
      <div class="analysis-conclusion-head">
        <span>${escapeHtml(audience.trendOnly ? audience.title : "روند ۷ روزه تحلیل")}</span>
        <strong>در حال بارگذاری...</strong>
      </div>
      <div class="trend-loading">در حال آماده‌سازی روند تاریخی</div>
    </section>`;
}

async function loadTrendAnalysis() {
  const container = document.getElementById("trendAnalysis");
  if (!container || state.view !== "underlying" || !state.analysisVisible || !state.underlyingKey) return;
  const requestId = ++state.trendRequestId;
  try {
    const query = appendQuery({ days: "7" });
    const data = await api(`/api/underlyings/${encodeURIComponent(state.underlyingKey)}/trend${query}`);
    if (requestId !== state.trendRequestId) return;
    container.outerHTML = renderTrendAnalysis(data);
    initTrendAnalysis(data);
  } catch (e) {
    if (requestId !== state.trendRequestId) return;
    destroyTrendChart();
    container.outerHTML = `
      <section class="analysis-conclusion trend-analysis">
        <div class="analysis-conclusion-head">
          <span>روند ۷ روزه تحلیل</span>
          <strong>خطا در دریافت داده</strong>
        </div>
        <div class="trend-loading">امکان ساخت روند تاریخی برای این تاریخ وجود ندارد</div>
      </section>`;
  }
}

function renderTrendAnalysis(data) {
  const items = data.items || [];
  const audience = activeAnalysisAudience();
  if (!items.length) {
    return `
      <section class="analysis-conclusion trend-analysis">
        <div class="analysis-conclusion-head">
          <span>${escapeHtml(audience.trendOnly ? audience.title : "روند ۷ روزه تحلیل")}</span>
          <strong>داده کافی نیست</strong>
        </div>
        <div class="trend-loading">برای این سهم در بازه انتخابی داده تاریخی کافی پیدا نشد</div>
      </section>`;
  }
  return `
    <section class="analysis-conclusion trend-analysis">
      <div class="analysis-conclusion-head">
        <span>${escapeHtml(audience.trendOnly ? audience.title : "روند ۷ روزه تحلیل")}</span>
        <strong>${fmtNum(items.length)} روز معاملاتی</strong>
      </div>
      <div class="trend-summary-grid">
        ${audience.prefixes.includes("natural") ? renderTrendSummary("حقیقی", data.summary?.natural, "analysis-person-natural") : ""}
        ${audience.prefixes.includes("legal") ? renderTrendSummary("حقوقی", data.summary?.legal, "analysis-person-legal") : ""}
      </div>
      ${renderTrendChartPanel()}
      <div class="trend-tables">
        ${audience.prefixes.includes("natural") ? renderTrendPersonTable("حقیقی", "natural", items, "analysis-person-natural") : ""}
        ${audience.prefixes.includes("legal") ? renderTrendPersonTable("حقوقی", "legal", items, "analysis-person-legal") : ""}
      </div>
    </section>`;
}

function renderTrendSummary(label, summary, cls) {
  const className = summary?.class_name || "neutral";
  return `
    <article class="trend-summary-card trend-${className}">
      <span class="${cls}">${escapeHtml(label)}</span>
      <strong>${escapeHtml(summary?.label || "داده کافی نیست")}</strong>
      <small>میانگین اخیر: ${summary?.average_score == null ? "—" : fmtRatio(summary.average_score)}</small>
    </article>`;
}

function renderTrendChartPanel() {
  return `
    <section class="trend-chart-panel">
      <div class="trend-chart-toolbar">
        <div class="trend-chart-tabs" id="trendMetricTabs" aria-label="فیلتر نمودار روند">
          ${TREND_METRIC_GROUPS.map(
            (group) => `
              <button type="button" class="trend-chart-tab ${state.trendMetricGroup === group.key ? "active" : ""}" data-metric-group="${escapeHtml(group.key)}">
                ${escapeHtml(group.label)}
              </button>`
          ).join("")}
        </div>
        <div class="trend-line-filters" id="trendLineFilters" aria-label="فیلتر خطوط نمودار"></div>
      </div>
      <div class="trend-chart-wrap">
        <canvas id="trendChart"></canvas>
      </div>
    </section>`;
}

function trendPeople() {
  const audience = activeAnalysisAudience();
  const people = [
    { key: "natural", label: "حقیقی", colorShift: 0 },
    { key: "legal", label: "حقوقی", colorShift: 1 },
  ];
  return people.filter((person) => audience.prefixes.includes(person.key));
}

function trendMetricValue(item, personKey, metricKey) {
  const person = item.people?.[personKey] || {};
  return asFiniteNumber(person[metricKey]);
}

function trendDatasetColor(metric, person) {
  if (person.key === "natural") return metric.color;
  const legalColors = {
    "#38bdf8": "#a78bfa",
    "#34d399": "#22d3ee",
    "#f97316": "#fb7185",
    "#f472b6": "#facc15",
    "#a78bfa": "#38bdf8",
    "#22d3ee": "#818cf8",
    "#facc15": "#f97316",
    "#fb7185": "#c084fc",
  };
  return legalColors[metric.color] || metric.color;
}

function buildTrendDatasets(items) {
  const group = activeTrendMetricGroup();
  return trendPeople().flatMap((person) =>
    group.metrics.map((metric) => {
      const color = trendDatasetColor(metric, person);
      return {
        label: `${person.label} · ${metric.label}`,
        data: items.map((item) => trendMetricValue(item, person.key, metric.key)),
        borderColor: color,
        backgroundColor: color,
        borderWidth: metric.key === "score" ? 3 : 2,
        tension: 0.32,
        pointRadius: 4,
        pointHoverRadius: 6,
        spanGaps: true,
      };
    })
  );
}

function destroyTrendChart() {
  if (state.trendChart) {
    state.trendChart.destroy();
    state.trendChart = null;
  }
}

function initTrendAnalysis(data) {
  document.querySelectorAll("#trendMetricTabs .trend-chart-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.trendMetricGroup = tab.dataset.metricGroup || "score";
      document.querySelectorAll("#trendMetricTabs .trend-chart-tab").forEach((el) => el.classList.remove("active"));
      tab.classList.add("active");
      renderTrendChart(data);
    });
  });
  renderTrendChart(data);
}

function renderTrendLineFilters(chart) {
  const container = document.getElementById("trendLineFilters");
  if (!container || !chart) return;
  container.innerHTML = chart.data.datasets
    .map((dataset, index) => {
      const active = chart.isDatasetVisible(index);
      return `
        <button
          type="button"
          class="trend-line-chip ${active ? "active" : ""}"
          data-dataset-index="${index}"
          style="--line-color: ${escapeHtml(dataset.borderColor)}"
        >
          ${escapeHtml(dataset.label)}
        </button>`;
    })
    .join("");
  container.querySelectorAll(".trend-line-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const index = Number(chip.dataset.datasetIndex);
      chart.setDatasetVisibility(index, !chart.isDatasetVisible(index));
      chart.update();
      renderTrendLineFilters(chart);
    });
  });
}

function renderTrendChart(data) {
  const canvas = document.getElementById("trendChart");
  if (!canvas) return;
  const palette = chartPalette();
  const items = data.items || [];
  const labels = items.map((item) => fmtDate(item.date));
  const datasets = buildTrendDatasets(items);
  destroyTrendChart();
  state.trendChart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      onClick(event, elements, chart) {
        if (!elements.length) return;
        const index = elements[0].datasetIndex;
        chart.setDatasetVisibility(index, !chart.isDatasetVisible(index));
        chart.update();
        renderTrendLineFilters(chart);
      },
      plugins: {
        legend: {
          labels: { color: palette.muted, font: { family: "Vazirmatn" }, usePointStyle: true },
          onClick(event, legendItem, legend) {
            const chart = legend.chart;
            const index = legendItem.datasetIndex;
            chart.setDatasetVisibility(index, !chart.isDatasetVisible(index));
            chart.update();
            renderTrendLineFilters(chart);
          },
        },
        tooltip: {
          rtl: true,
          titleFont: { family: "Vazirmatn" },
          bodyFont: { family: "Vazirmatn" },
        },
      },
      scales: {
        x: { ticks: { color: palette.muted, font: { family: "Vazirmatn" } }, grid: { color: palette.grid } },
        y: { ticks: { color: palette.muted }, grid: { color: palette.grid } },
      },
    },
  });
  renderTrendLineFilters(state.trendChart);
}

function renderTrendPersonTable(label, key, items, cls) {
  return `
    <section class="trend-person">
      <div class="trend-person-title">
        <span class="${cls}">${escapeHtml(label)}</span>
        <strong>اعداد روزانه</strong>
      </div>
      <div class="trend-table-wrap">
        <table class="trend-table">
          <thead>
            <tr>
              <th>تاریخ</th>
              <th>نتیجه</th>
              <th>Call خ/ف</th>
              <th>Put خ/ف</th>
              <th>ITM/OTM</th>
              <th>Call/Put</th>
              <th>تغییر OI</th>
            </tr>
          </thead>
          <tbody>
            ${items.map((item) => renderTrendRow(item, key)).join("")}
          </tbody>
        </table>
      </div>
    </section>`;
}

function renderTrendRow(item, key) {
  const person = item.people?.[key] || {};
  const className = person.class_name || "neutral";
  const oi = person.has_open_interest ? fmtNum(person.open_interest_change) : "—";
  return `
    <tr>
      <td>${escapeHtml(fmtDate(item.date))}</td>
      <td><span class="trend-badge trend-${className}">${escapeHtml(person.label || "—")}</span></td>
      <td>${fmtNum(person.call_buy)} / ${fmtNum(person.call_sell)}</td>
      <td>${fmtNum(person.put_buy)} / ${fmtNum(person.put_sell)}</td>
      <td>${fmtNum(person.itm_volume)} / ${fmtNum(person.otm_volume)}</td>
      <td>${person.call_put_ratio == null ? "—" : fmtRatio(person.call_put_ratio)}</td>
      <td>${escapeHtml(oi)}</td>
    </tr>`;
}

function renderAnalysisSideSummary(typeModel) {
  const audience = activeAnalysisAudience();
  const rows = [
    ["حقیقی", "natural", "analysis-person-natural"],
    ["حقوقی", "legal", "analysis-person-legal"],
  ].filter(([, prefix]) => audience.prefixes.includes(prefix));

  return `
    <section class="analysis-side-summary">
      <div class="analysis-side-summary-title">خلاصه</div>
      <div class="analysis-summary-list">
        ${rows
          .map(
            ([person, prefix, cls]) => `
              <article class="analysis-summary-row">
                <div class="analysis-summary-person ${cls}">${person}</div>
                <div class="analysis-summary-groups">
                  ${renderSummaryGroup("ITM", typeModel.ITM, prefix)}
                  ${renderSummaryGroup("OTM", typeModel.OTM, prefix)}
                  ${renderCombinedSummaryGroup(typeModel.ITM, typeModel.OTM, prefix)}
                </div>
              </article>`
          )
          .join("")}
      </div>
    </section>`;
}

function combinedMetric(a, b, prefix) {
  return {
    count: combineAnalysisField(a, b, `${prefix}_count`),
    volume: combineAnalysisField(a, b, `${prefix}_volume`),
    value: combineAnalysisField(a, b, `${prefix}_value`),
  };
}

function hasAnalysisField(bucket, field) {
  return (bucket?._presentFields?.[field] || 0) > 0;
}

function analysisFieldValue(bucket, field) {
  return hasAnalysisField(bucket, field) ? bucket[field] : null;
}

function combineAnalysisField(a, b, field) {
  const hasA = hasAnalysisField(a, field);
  const hasB = hasAnalysisField(b, field);
  if (!hasA && !hasB) return null;
  return (hasA ? numericValue(a[field]) : 0) + (hasB ? numericValue(b[field]) : 0);
}

function renderCombinedSummaryMetric(a, b, prefix) {
  const metric = combinedMetric(a, b, prefix);
  return `
    <span class="analysis-metric analysis-metric-total">
      <strong>${analysisMetricValue(metric.volume)}</strong>
    </span>`;
}

function renderSummaryGroup(label, bucket, prefix) {
  return `
    <div class="analysis-summary-group">
      <div class="analysis-summary-group-title">
        <span>${label}</span>
      </div>
      <div class="analysis-summary-pair">
        ${renderSummaryTile("خرید", bucket, `${prefix}_buy`)}
        ${renderSummaryTile("فروش", bucket, `${prefix}_sell`)}
        ${renderOpenInterestTile(bucket.open_interest_positions)}
      </div>
    </div>`;
}

function renderCombinedSummaryGroup(itm, otm, prefix) {
  const openInterest = combineAnalysisField(itm, otm, "open_interest_positions");
  return `
    <div class="analysis-summary-group analysis-summary-group-total">
      <div class="analysis-summary-group-title">
        <span>جمع</span>
      </div>
      <div class="analysis-summary-pair">
        ${renderCombinedSummaryTile("خرید", itm, otm, `${prefix}_buy`)}
        ${renderCombinedSummaryTile("فروش", itm, otm, `${prefix}_sell`)}
        ${renderOpenInterestTile(openInterest)}
      </div>
    </div>`;
}

function renderSummaryTile(label, bucket, prefix) {
  return `
    <div class="analysis-summary-tile">
      <span>${label}</span>
      ${renderSummaryMetric(bucket, prefix)}
    </div>`;
}

function renderCombinedSummaryTile(label, itm, otm, prefix) {
  return `
    <div class="analysis-summary-tile">
      <span>${label}</span>
      ${renderCombinedSummaryMetric(itm, otm, prefix)}
    </div>`;
}

function renderOpenInterestTile(value) {
  return `
    <div class="analysis-summary-tile analysis-summary-oi-tile">
      <span>موقعیت</span>
      <strong>${analysisMetricValue(value)}</strong>
    </div>`;
}

function renderSummaryMetric(bucket, prefix) {
  const value = analysisFieldValue(bucket, `${prefix}_volume`);
  return `
    <span class="analysis-metric">
      <strong>${analysisMetricValue(value)}</strong>
    </span>`;
}

function renderAnalysisRows(bucket) {
  const audience = activeAnalysisAudience();
  const rows = [
    ["حقیقی", "خرید", "natural_buy_count", "natural_buy_volume", "natural_buy_value", "analysis-person-natural"],
    ["حقیقی", "فروش", "natural_sell_count", "natural_sell_volume", "natural_sell_value", "analysis-person-natural"],
    ["حقوقی", "خرید", "legal_buy_count", "legal_buy_volume", "legal_buy_value", "analysis-person-legal"],
    ["حقوقی", "فروش", "legal_sell_count", "legal_sell_volume", "legal_sell_value", "analysis-person-legal"],
  ].filter((row) => audience.prefixes.some((prefix) => row[2].startsWith(prefix)));

  return rows
    .map(
      ([person, side, countKey, volumeKey, valueKey, cls]) => `
        <tr>
          <td class="${cls}">${person}</td>
          <td>${side}</td>
          <td>${analysisMetricValue(analysisFieldValue(bucket, countKey))}</td>
          <td>${analysisMetricValue(analysisFieldValue(bucket, volumeKey))}</td>
          <td>${analysisMetricValue(analysisFieldValue(bucket, valueKey))}</td>
        </tr>`
    )
    .join("");
}

function renderAnalysisBucket(label, bucket) {
  return `
    <section class="analysis-bucket">
      <div class="analysis-bucket-header">
        <span>${label}</span>
        <span>${fmtNum(bucket.contract_count)} قرارداد</span>
      </div>
      <div class="analysis-oi-summary">
        <div>
          <span>موقعیت باز</span>
          <strong>${analysisMetricValue(analysisFieldValue(bucket, "buy_open_positions"))}</strong>
        </div>
        <div>
          <span>فروش باز</span>
          <strong>${analysisMetricValue(analysisFieldValue(bucket, "sell_open_positions"))}</strong>
        </div>
        <div>
          <span>موقعیت دیروز</span>
          <strong>${analysisMetricValue(analysisFieldValue(bucket, "yesterday_open_positions"))}</strong>
        </div>
      </div>
      <div class="analysis-table-wrap">
        <table class="analysis-table">
          <thead>
            <tr>
              <th>گروه</th>
              <th>سمت</th>
              <th>تعداد</th>
              <th>حجم</th>
              <th>ارزش</th>
            </tr>
          </thead>
          <tbody>${renderAnalysisRows(bucket)}</tbody>
        </table>
      </div>
    </section>`;
}

function renderAnalysisSide(type, title, model) {
  const total = model[type].ITM.contract_count + model[type].OTM.contract_count;
  return `
    <section class="analysis-side">
      <div class="analysis-side-title">
        <span>${title}</span>
        <span>${fmtNum(total)} قرارداد ITM/OTM</span>
      </div>
      ${renderAnalysisSideSummary(model[type])}
      <div class="analysis-buckets">
        ${renderAnalysisBucket("ITM", model[type].ITM)}
        ${renderAnalysisBucket("OTM", model[type].OTM)}
      </div>
    </section>`;
}

function renderAnalysisAudienceTabs() {
  return `
    <div class="analysis-audience-tabs" id="analysisAudienceTabs" role="tablist" aria-label="نوع آنالیز">
      ${analysisAudienceItems()
        .map(
          (item) => `
            <button
              type="button"
              class="analysis-audience-tab ${state.analysisAudience === item.key ? "active" : ""}"
              data-audience="${escapeHtml(item.key)}"
              role="tab"
              aria-selected="${state.analysisAudience === item.key ? "true" : "false"}"
            >${escapeHtml(item.label)}</button>`
        )
        .join("")}
    </div>`;
}

function bindAnalysisAudienceTabs() {
  document.querySelectorAll("#analysisAudienceTabs .analysis-audience-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.analysisAudience = tab.dataset.audience || "both";
      renderAnalysis();
    });
  });
}

function renderAnalysis() {
  const panel = document.getElementById("analysisPanel");
  const content = document.getElementById("analysisContent");
  if (!panel || !content) return;
  if (state.view !== "underlying" || !state.analysisVisible) {
    state.trendRequestId += 1;
    destroyTrendChart();
    panel.classList.add("hidden");
    return;
  }

  applyCachedClientTypes(state.items);
  const filteredRows = applyLocalFilters(state.items);
  const rows = filteredRows.filter((row) => row.moneyness === "ITM" || row.moneyness === "OTM");
  const model = buildAnalysisModel(rows);
  const underlyingName = state.underlying?.underlying_symbol || state.underlying?.underlying_short_name || "";
  const audience = activeAnalysisAudience();
  const audienceTitle = audience.title;
  setText("analysisTitle", underlyingName
    ? `آنالیز ${audienceTitle} ${underlyingName}${state.selectedDate ? ` - ${fmtDate(state.selectedDate)}` : ""}`
    : `آنالیز ${audienceTitle}`);
  setText("analysisScope", audience.trendOnly ? "۷ روزه" : `${fmtNum(rows.length)} قرارداد ITM/OTM`);
  const analysisBody = audience.trendOnly
    ? renderTrendShell()
    : renderFourStepConclusion(rows) + renderAnalysisSide("call", "اختیار خرید", model) + renderAnalysisSide("put", "اختیار فروش", model);
  destroyTrendChart();
  content.innerHTML = renderAnalysisAudienceTabs() + analysisBody;
  panel.classList.remove("hidden");
  bindAnalysisAudienceTabs();
  if (audience.trendOnly) loadTrendAnalysis();
}

function exportCsv() {
  if (!state.filtered.length) {
    showToast("داده برای خروجی موجود نیست", "error");
    return;
  }
  const config = VIEW_CONFIG[state.view];
  const keys = config.columns.map((c) => c.key);
  const escapeCsv = (value) => {
    const s = value == null ? "" : String(value);
    return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
  };
  const header = config.columns.map((c) => escapeCsv(c.label)).join(",");
  const rows = state.filtered.map((row) =>
    keys.map((k) => {
      return escapeCsv(row[k]);
    }).join(",")
  );
  const bom = "\uFEFF";
  const blob = new Blob([bom + header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a");
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = `tsetmc_${state.view}_${Date.now()}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  showToast("فایل CSV دانلود شد");
}

let refreshPollTimer = null;

function setRefreshControlsLoading(on) {
  document.getElementById("btnRefresh")?.classList.toggle("loading", on);
  if (document.getElementById("btnRefresh")) document.getElementById("btnRefresh").disabled = on;
  if (document.getElementById("btnEmptyRefresh")) document.getElementById("btnEmptyRefresh").disabled = on;
}

async function startRefresh() {
  setRefreshControlsLoading(true);
  setStatusText("به‌روزرسانی داده شروع شد...");
  try {
    const res = await api("/api/refresh", { method: "POST" });
    if (res.status === "already_running") {
      showToast("به‌روزرسانی در حال انجام است...", "success");
    } else if (res.status === "cooldown") {
      showToast("کمی صبر کنید و دوباره تلاش کنید", "error");
      setRefreshControlsLoading(false);
      return;
    } else {
      showToast("به‌روزرسانی شروع شد — ممکن است چند دقیقه طول بکشد");
    }
    pollRefreshStatus();
  } catch (e) {
    showToast("خطا در شروع به‌روزرسانی", "error");
    setRefreshControlsLoading(false);
  }
}

function pollRefreshStatus() {
  clearInterval(refreshPollTimer);
  refreshPollTimer = setInterval(async () => {
    try {
      const st = await api("/api/refresh/status");
      if (st.message) setStatusText(st.message);
      if (st.running) {
        await loadSummary();
        if (st.message) setStatusText(st.message);
        await reloadActiveData();
        return;
      }
      clearInterval(refreshPollTimer);
      setRefreshControlsLoading(false);
      if (st.last_error) {
        showToast(`خطا: ${st.last_error}`, "error");
        await loadSummary();
        await reloadActiveData();
      } else if (st.last_result) {
        showToast(`انجام شد — ${st.last_result.options} قرارداد`);
        state.selectedDate = "";
        state.latestDate = "";
        syncDateToUrl();
        await init();
      }
    } catch {
      clearInterval(refreshPollTimer);
      setRefreshControlsLoading(false);
      showToast("ارتباط با وضعیت به‌روزرسانی قطع شد", "error");
    }
  }, 3000);
}

function bindSearch() {
  const input = document.getElementById("searchInput");
  if (!input) return;
  let debounce;
  input.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => reloadActiveData(), 280);
  });
}

function bindFilters() {
  document.getElementById("dateFilter")?.addEventListener("change", async (event) => {
    await changeSelectedDate(event.target.value);
  });
  document.getElementById("datePickerButton")?.addEventListener("click", () => {
    setDatePickerVisible(!state.calendarVisible);
  });
  document.getElementById("datePickerPrev")?.addEventListener("click", () => shiftCalendarMonth(-1));
  document.getElementById("datePickerNext")?.addEventListener("click", () => shiftCalendarMonth(1));
  document.addEventListener("click", (event) => {
    if (!state.calendarVisible) return;
    if (event.target.closest(".date-filter") || event.target.closest("#datePickerPopover")) return;
    setDatePickerVisible(false);
  });

  document.getElementById("btnAnalysis")?.addEventListener("click", toggleAnalysisMode);

  document.querySelectorAll("#typeFilter .segment").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#typeFilter .segment").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      state.filters.type = btn.dataset.type;
      clearSelectionForFilterChange();
      applyFilterAndSort();
    });
  });

  document.getElementById("expiryFilter")?.addEventListener("change", (event) => {
    state.filters.expiry = event.target.value;
    clearSelectionForFilterChange();
    applyFilterAndSort();
  });
  document.getElementById("moneynessFilter")?.addEventListener("change", (event) => {
    state.filters.moneyness = event.target.value;
    clearSelectionForFilterChange();
    applyFilterAndSort();
  });
  document.getElementById("strikeFilter")?.addEventListener("change", (event) => {
    state.filters.strike = event.target.value;
    clearSelectionForFilterChange();
    applyFilterAndSort();
  });
  document.getElementById("btnClearFilters")?.addEventListener("click", resetFilters);
}

async function init() {
  setLoading(true);
  try {
    updateViewChrome();
    const activated = await ensureActivation();
    if (!activated) return;
    await loadDates();
    await loadSummary();
    await reloadActiveData();
  } catch (e) {
    showToast("خطا در بارگذاری داده", "error");
    state.items = [];
    state.filtered = [];
    renderTable();
    setEmptyMessage("خطا در بارگذاری داده", "برنامه نتوانست داده‌ها را دریافت کند؛ دوباره تلاش کنید");
    console.error(e);
  } finally {
    setLoading(false);
  }
}

function bindActivation() {
  document.getElementById("activationForm")?.addEventListener("submit", submitActivationCode);
}

document.getElementById("btnRefresh")?.addEventListener("click", startRefresh);
document.getElementById("btnEmptyRefresh")?.addEventListener("click", startRefresh);
document.getElementById("btnExport")?.addEventListener("click", exportCsv);
document.getElementById("btnBack")?.addEventListener("click", goBackToUnderlyings);
document.getElementById("closeDetail")?.addEventListener("click", closeDetailSheet);
document.getElementById("detailBackdrop")?.addEventListener("click", closeDetailSheet);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.calendarVisible) {
    setDatePickerVisible(false);
    return;
  }
  if (!state.selectedRowKey) return;
  closeDetailSheet();
});
window.addEventListener("resize", () => {
  if (state.view !== "underlyings" || isMobileLayout() || !state.selectedRowKey) return;
  closeDetailSheet();
});

bindSearch();
bindFilters();
bindActivation();
bindThemeToggle();
init();
