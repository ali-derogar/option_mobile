/** Manual whole-market expiry panel. Calendar updates only read the local API. */
(() => {
  const root = document.getElementById("expirySection");
  if (!root || /\/underlying\//.test(window.location.pathname)) return;

  const toggle = document.getElementById("btnExpiries");
  const panel = document.getElementById("expiryPanel");
  const badge = document.getElementById("expiryBadge");
  const refreshButton = document.getElementById("btnExpiryRefresh");
  const list = document.getElementById("expiryList");
  const filterControls = [...document.querySelectorAll("[data-expiry-filter]")];
  const underlyingFilter = document.getElementById("expiryUnderlyingFilter");
  const clearFiltersButton = document.getElementById("btnExpiryClearFilters");
  const expandedContracts = new Set();
  // Filters belong to this page visit; a reload starts without restrictions.
  filterControls.forEach((control) => { control.value = ""; });
  const REQUEST_TIMEOUT_MS = 15000;
  const numberFormat = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 0 });
  const scoreFormat = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 2 });
  const dayFormat = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tehran", year: "numeric", month: "2-digit", day: "2-digit",
  });
  const expiryFormat = new Intl.DateTimeFormat("fa-IR", {
    calendar: "persian", timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit",
  });
  const updateFormat = new Intl.DateTimeFormat("fa-IR", {
    timeZone: "Asia/Tehran", dateStyle: "short", timeStyle: "short",
  });

  let active = false;
  let snapshot = null;
  let starting = false;
  let running = false;
  let errorMessage = "";
  let startErrorMessage = "";
  let failures = 0;
  let lifecycle = 0;
  let readId = 0;
  let pollTimer = null;
  let dayTimer = null;
  let filterTimer = null;
  let filterError = "";
  let filterPending = false;
  let lastValidQuery = "";

  function readFilters() {
    const params = new URLSearchParams();
    filterError = "";
    for (const control of filterControls) {
      control.removeAttribute("aria-invalid");
      let value = control.value.trim();
      if (!value) continue;
      if (control.tagName === "INPUT") {
        value = value.replace(/[۰-۹٠-٩]/g, (digit) => String("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩".indexOf(digit) % 10))
          .replace(/[,٬،\s]/g, "").replace(/٫/g, ".");
        const score = control.dataset.expiryFilter === "min_score";
        const valid = score ? /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) && Number.isFinite(Number(value))
          : /^\d+$/.test(value) && Number.isSafeInteger(Number(value));
        if (!valid) {
          control.setAttribute("aria-invalid", "true");
          filterError = "مقادیر فیلتر باید عدد معتبر باشند؛ روز و حجم باید عدد صحیح نامنفی باشند.";
          continue;
        }
      }
      params.set(control.dataset.expiryFilter, value);
    }
    if (params.has("max_days") && Number(params.get("max_days")) < Number(params.get("min_days") || 0)) {
      filterError = "حداکثر روز تا سررسید نباید از حداقل کمتر باشد.";
      filterControls.find((control) => control.dataset.expiryFilter === "max_days").setAttribute("aria-invalid", "true");
    }
    return filterError ? null : params;
  }

  function renderUnderlyingOptions() {
    const selected = underlyingFilter.value;
    const options = new Map((snapshot?.underlyings || []).map((row) => [row.ins_code, row.symbol]));
    if (selected && !options.has(selected)) {
      options.set(selected, underlyingFilter.selectedOptions[0]?.textContent || selected);
    }
    const signature = JSON.stringify([...options]);
    if (underlyingFilter.dataset.options === signature) return;
    const fragment = document.createDocumentFragment();
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "همهٔ سهم‌ها";
    fragment.append(all);
    for (const [value, text] of options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      fragment.append(option);
    }
    underlyingFilter.replaceChildren(fragment);
    underlyingFilter.value = selected;
    underlyingFilter.dataset.options = signature;
  }

  async function expiryApi(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      return await api(path, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  function marketDate() {
    const parts = Object.fromEntries(dayFormat.formatToParts(new Date()).map(({ type, value }) => [type, value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  function daysLabel(days) {
    return days === 0 ? "امروز" : `${numberFormat.format(days)} روز`;
  }

  function priceLabel(value) {
    return value == null ? "—" : numberFormat.format(value);
  }

  function setBadge(element, days) {
    const tone = days <= 1 ? "urgent" : days <= 5 ? "soon" : "safe";
    element.className = `expiry-badge expiry-${tone}`;
    element.textContent = daysLabel(days);
    element.setAttribute("aria-label", days === 0 ? "روز سررسید: امروز" : `${daysLabel(days)} تا سررسید`);
  }

  function setPendingCalendarBadge(element) {
    element.className = "expiry-badge";
    element.textContent = "نیازمند بررسی";
    element.setAttribute("aria-label", "روزشمار هنوز برای روز جاری بررسی نشده است");
  }

  function addMetric(parent, label, value) {
    const item = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value ?? "—";
    item.append(term, description);
    parent.append(item);
  }

  function renderRows(calendarCurrent, pricingCurrent) {
    const scrollTop = list.scrollTop;
    // Read the native disclosure state before replacing rows. This also keeps
    // clicks that have not dispatched a queued `toggle` event yet.
    for (const details of list.querySelectorAll(".expiry-contract-details")) {
      if (details.open) expandedContracts.add(details.dataset.insCode);
      else expandedContracts.delete(details.dataset.insCode);
    }
    const focusedCode = list.contains(document.activeElement)
      ? document.activeElement.closest(".expiry-contract-details")?.dataset.insCode : null;
    let focusTarget = null;
    const fragment = document.createDocumentFragment();
    for (const row of snapshot?.items || []) {
      const card = document.createElement("li");
      card.className = "expiry-card";
      card.classList.toggle("expiry-arbitrage-candidate", pricingCurrent && row.is_arbitrage_candidate === true);
      const details = document.createElement("details");
      details.className = "expiry-contract-details";
      details.dataset.insCode = String(row.ins_code);
      details.open = expandedContracts.has(details.dataset.insCode);
      const heading = document.createElement("summary");
      heading.className = "expiry-card-head";
      if (details.dataset.insCode === focusedCode) focusTarget = heading;
      const symbol = document.createElement("strong");
      symbol.textContent = row.symbol;
      const remaining = document.createElement("span");
      if (calendarCurrent) setBadge(remaining, row.days_remaining);
      else setPendingCalendarBadge(remaining);
      heading.append(symbol, remaining);
      const metrics = document.createElement("dl");
      metrics.className = "expiry-metrics";
      const rawDate = String(row.end_date);
      const date = new Date(Date.UTC(Number(rawDate.slice(0, 4)), Number(rawDate.slice(4, 6)) - 1, Number(rawDate.slice(6))));
      addMetric(metrics, "سهم پایه", row.underlying_symbol);
      addMetric(metrics, "نوع", row.option_type === "call" ? "اختیار خرید" : "اختیار فروش");
      addMetric(metrics, "قیمت اعمال", priceLabel(row.strike_price));
      addMetric(metrics, "سررسید", expiryFormat.format(date));
      addMetric(metrics, "بهترین عرضه", priceLabel(row.ask_price));
      addMetric(metrics, "حجم بهترین عرضه", priceLabel(row.ask_volume));
      addMetric(metrics, "کمترین قیمت معامله‌شده امروز (ریال)", calendarCurrent ? priceLabel(row.price_min) : "نیازمند به‌روزرسانی");
      addMetric(metrics, "بیشترین قیمت معامله‌شده امروز (ریال)", calendarCurrent ? priceLabel(row.price_max) : "نیازمند به‌روزرسانی");
      addMetric(metrics, "آخرین قیمت سهم پایه", priceLabel(row.underlying_last_price));
      addMetric(metrics, "قیمت پایانی سهم پایه", priceLabel(row.underlying_closing_price));
      addMetric(metrics, "ارزش ذاتی", priceLabel(row.intrinsic_value));
      addMetric(metrics, "دریافتی نقدی برآوردی هر واحد (ریال)", !pricingCurrent ? "نیازمند به‌روزرسانی"
        : priceLabel(row.estimated_cash_value_per_unit));
      addMetric(metrics, "سود هر واحد (ریال)؛ برآورد ناخالص پیش از کارمزد", !pricingCurrent ? "نیازمند به‌روزرسانی"
        : priceLabel(row.gross_profit_per_unit));
      addMetric(metrics, "بازده ناخالص برآوردی (%)", !pricingCurrent ? "نیازمند به‌روزرسانی"
        : row.intrinsic_gap_percent == null ? "نامشخص" : `${scoreFormat.format(row.intrinsic_gap_percent)}٪`);
      addMetric(metrics, "فرصت آربیتراژ", !pricingCurrent ? "نیازمند به‌روزرسانی"
        : row.is_arbitrage_candidate === true ? "فرصت بالقوه"
          : row.gross_profit_per_unit == null ? "خارج از دامنه یا داده ناکافی" : "فرصتی شناسایی نشد");
      addMetric(metrics, "حجم معاملات", priceLabel(row.trade_volume));
      addMetric(metrics, "وضعیت", row.moneyness === "unknown" ? "نامشخص" : row.moneyness);
      details.append(heading, metrics);
      card.append(details);
      fragment.append(card);
    }
    list.replaceChildren(fragment);
    focusTarget?.focus({ preventScroll: true });
    // Replacing the focused disclosure can move the scroll anchor. Restore
    // the reader's position after layout and native focus reconciliation.
    list.scrollTop = scrollTop;
  }

  function render() {
    const busy = starting || running;
    const partial = Boolean(snapshot?.partial);
    const calendarCurrent = snapshot?.as_of_date === marketDate();
    const calendarPending = Boolean((snapshot?.fetched_at || partial) && !calendarCurrent);
    const pricingCurrent = Boolean(snapshot?.pricing_current && calendarCurrent);
    refreshButton.disabled = busy;
    refreshButton.textContent = busy ? "در حال به‌روزرسانی…" : "به‌روزرسانی سررسیدها";
    document.getElementById("expiryLastUpdate").textContent = snapshot?.fetched_at
      ? `${partial ? "آخرین دریافت کامل" : "آخرین دریافت"}: ${updateFormat.format(new Date(snapshot.fetched_at))}`
      : partial ? "در حال تکمیل نخستین دریافت…" : "هنوز دریافت نشده";
    if (snapshot?.fetched_at || (partial && Number.isInteger(snapshot.nearest_days))) {
      badge.classList.remove("hidden");
      if (!calendarCurrent) {
        setPendingCalendarBadge(badge);
      } else if (Number.isInteger(snapshot.nearest_days)) {
        setBadge(badge, snapshot.nearest_days);
      } else {
        badge.className = "expiry-badge";
        badge.textContent = partial ? "در حال بررسی" : "بدون سررسید";
        badge.setAttribute("aria-label", partial ? "بررسی سررسیدها ادامه دارد" : "سررسید پیش‌رو موجود نیست");
      }
      badge.title = !calendarCurrent ? "روز تقویمی تغییر کرده؛ روزشمار نیازمند بررسی دوباره است"
        : partial ? "نتیجهٔ موقت؛ بررسی سررسیدها ادامه دارد" : "بر اساس آخرین دریافت و تاریخ امروز به وقت تهران";
    } else {
      badge.className = "expiry-badge hidden";
    }
    document.getElementById("expiryCount").textContent = snapshot?.fetched_at || partial
      ? `${numberFormat.format(snapshot.items.length)} از ${numberFormat.format(snapshot.total)} قرارداد` : "";
    renderUnderlyingOptions();
    const filterCount = filterControls.filter((control) => control.value.trim()).length;
    clearFiltersButton.disabled = filterCount === 0;
    const filterCountLabel = document.getElementById("expiryFilterCount");
    filterCountLabel.textContent = filterCount ? `${numberFormat.format(filterCount)} فیلتر انتخاب‌شده` : "";
    filterCountLabel.classList.toggle("hidden", filterCount === 0);
    const filterWarning = document.getElementById("expiryFilterError");
    filterWarning.textContent = filterError;
    filterWarning.classList.toggle("hidden", !filterError);
    const pricingNote = document.getElementById("expiryPricingNote");
    pricingNote.classList.toggle("hidden", !snapshot?.fetched_at && !partial);
    pricingNote.textContent = (pricingCurrent
      ? `مبنای برآورد: قیمت پایانی سهم پایه در دریافت ${updateFormat.format(new Date(snapshot.market_received_at))}؛ پیش از کارمزد.`
      : "برای محاسبهٔ برآورد، سررسیدها را امروز به‌روزرسانی کنید.")
      + " بررسی آربیتراژ فقط برای اختیار خرید با سررسید امروز است؛ تاریخ سررسید، امکان تسویه نقدی همان روز را تأیید نمی‌کند."
      + " کمینه و بیشینه، قیمت معاملات انجام‌شده امروزند؛ مبنای خرید، بهترین عرضه فعلی است. بازه ناموجود با — نمایش داده می‌شود.";
    const refresh = !starting && snapshot?.refresh?.running ? snapshot.refresh : {};
    const total = refresh.total || 0;
    const processed = refresh.processed || 0;
    const percent = total ? Math.min(100, Math.floor(processed * 100 / total)) : 0;
    const progress = document.getElementById("expiryProgress");
    progress.classList.toggle("hidden", !busy);
    if (total) progress.value = percent;
    else progress.removeAttribute("value");
    document.getElementById("expiryPreviewNote").classList.toggle("hidden", !partial);
    document.getElementById("expiryStatus").textContent = busy
      ? refresh.stage === "saving" ? "در حال ذخیرهٔ نتیجهٔ نهایی…"
        : total ? `بررسی ${numberFormat.format(processed)} از ${numberFormat.format(total)} قرارداد (${numberFormat.format(percent)}٪)`
          : "در حال دریافت فهرست اولیهٔ بازار…"
      : filterPending && !filterError ? "در حال اعمال فیلترها…"
        : calendarPending ? "اطلاعات قبلی نمایش داده می‌شوند؛ وضعیت روز جاری هنوز تأیید نشده است."
        : partial ? "نتایج موقت؛ برای بررسی وضعیت دوباره تلاش کنید."
        : !snapshot?.fetched_at ? "ابتدا سررسیدها را به‌روزرسانی کنید"
        : snapshot.items.length ? "به ترتیب نزدیک‌ترین سررسید؛ روزهای تقویمی به وقت تهران"
          : snapshot.total_upcoming && !pricingCurrent && filterControls.some((control) =>
            ["min_score", "arbitrage_only"].includes(control.dataset.expiryFilter) && control.value.trim())
            ? "فیلتر بازده و آربیتراژ نیازمند به‌روزرسانی اطلاعات امروز است."
            : snapshot.total_upcoming ? "نتیجه‌ای مطابق فیلترها نیست" : "سررسید پیش‌رو موجود نیست";
    const warning = document.getElementById("expiryWarning");
    const calendarWarning = calendarPending ? "روز تقویمی تغییر کرده؛ روزشمار تا خواندن اطلاعات روز جاری نیازمند بررسی است." : "";
    warning.textContent = [calendarWarning, startErrorMessage, errorMessage, snapshot?.refresh?.last_error, snapshot?.warning].filter(Boolean).join(" ");
    warning.classList.toggle("hidden", !warning.textContent);
    renderRows(calendarCurrent, pricingCurrent);
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(loadSnapshot, 3000);
  }

  async function loadSnapshot() {
    if (!active || starting) return;
    // Expire the cached calendar view immediately, even if this read hangs.
    if (snapshot && snapshot.as_of_date !== marketDate()) render();
    clearTimeout(filterTimer);
    const params = readFilters();
    if (params) lastValidQuery = params.toString();
    const id = ++readId;
    clearTimeout(pollTimer);
    try {
      const query = lastValidQuery;
      const response = await expiryApi(`/api/expiries${query ? `?${query}` : ""}`);
      if (!active || id !== readId) return;
      running = Boolean(response.refresh?.running);
      if (running || response.refresh?.last_error || (response.fetched_at ?? null) !== (snapshot?.fetched_at ?? null)) {
        startErrorMessage = "";
      }
      snapshot = response;
      errorMessage = "";
      failures = 0;
      filterPending = !params;
      render();
      if (running) schedulePoll();
    } catch {
      if (!active || id !== readId) return;
      failures += 1;
      if (failures >= 3) {
        running = false;
        filterPending = false;
      }
      errorMessage = "خواندن وضعیت سررسیدها ناموفق بود؛ دوباره تلاش کنید.";
      render();
      if (failures < 3) schedulePoll();
    }
  }

  function changeFilters(debounce = false) {
    // Invalidate old responses immediately, including during the debounce.
    readId += 1;
    clearTimeout(pollTimer);
    clearTimeout(filterTimer);
    filterPending = true;
    failures = 0;
    readFilters();
    render();
    // Invalid draft values must not cancel status reconciliation: the server
    // may have accepted a refresh whose POST response was lost. loadSnapshot
    // keeps using the last valid query until the draft is corrected.
    if (active) {
      if (debounce) filterTimer = setTimeout(loadSnapshot, 300);
      else loadSnapshot();
    }
  }

  async function startRefresh() {
    if (!active || starting || running) return;
    const generation = lifecycle;
    readId += 1;
    clearTimeout(pollTimer);
    starting = true;
    errorMessage = "";
    startErrorMessage = "";
    failures = 0;
    render();
    try {
      const response = await expiryApi("/api/expiries/refresh", { method: "POST" });
      if (!active || generation !== lifecycle) {
        // An old POST can still change the server after the page is restored.
        loadSnapshot();
        return;
      }
      starting = false;
      if (response.status === "cooldown") {
        errorMessage = "بین شروع به‌روزرسانی‌ها حداقل یک دقیقه صبر کنید.";
        render();
        // The previous job may have completed while status reads were offline.
        schedulePoll();
        return;
      }
      running = true;
      failures = 0;
      render();
      await loadSnapshot();
    } catch {
      if (!active || generation !== lifecycle) {
        loadSnapshot();
        return;
      }
      starting = false;
      running = false;
      startErrorMessage = "شروع به‌روزرسانی سررسیدها تأیید نشد؛ دوباره تلاش کنید.";
      render();
      // A lost response does not mean the server rejected the POST. Reconcile
      // using GET, without starting another market fetch.
      schedulePoll();
    }
  }

  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(expanded));
    panel.classList.toggle("hidden", !expanded);
  });
  refreshButton.addEventListener("click", startRefresh);
  for (const control of filterControls) {
    control.addEventListener(control.tagName === "INPUT" ? "input" : "change", () => changeFilters(control.tagName === "INPUT"));
  }
  clearFiltersButton.addEventListener("click", () => {
    filterControls.forEach((control) => { control.value = ""; });
    changeFilters();
  });
  window.addEventListener("pagehide", () => {
    active = false;
    starting = false;
    lifecycle += 1;
    readId += 1;
    clearTimeout(pollTimer);
    clearInterval(dayTimer);
    clearTimeout(filterTimer);
    dayTimer = null;
  });
  window.addEventListener("focus", () => { if (active) loadSnapshot(); });
  document.addEventListener("visibilitychange", () => {
    if (active && document.visibilityState === "visible") loadSnapshot();
  });

  window.ExpiryPanel = {
    initialize() {
      active = true;
      root.classList.remove("hidden");
      if (dayTimer === null) {
        dayTimer = setInterval(() => {
          if (snapshot?.as_of_date !== marketDate()) {
            render();
            loadSnapshot();
          }
        }, 60000);
      }
      loadSnapshot();
    },
  };
})();
