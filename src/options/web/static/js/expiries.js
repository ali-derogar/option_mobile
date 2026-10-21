/** Manual whole-market expiry panel. Calendar updates only read the local API. */
(() => {
  const root = document.getElementById("expirySection");
  if (!root || /\/underlying\//.test(window.location.pathname)) return;

  const toggle = document.getElementById("btnExpiries");
  const panel = document.getElementById("expiryPanel");
  const badge = document.getElementById("expiryBadge");
  const refreshButton = document.getElementById("btnExpiryRefresh");
  const list = document.getElementById("expiryList");
  const numberFormat = new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 0 });
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

  function marketDate() {
    const parts = Object.fromEntries(dayFormat.formatToParts(new Date()).map(({ type, value }) => [type, value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  function daysLabel(days) {
    return days === 0 ? "امروز" : `${numberFormat.format(days)} روز`;
  }

  function setBadge(element, days) {
    const tone = days <= 1 ? "urgent" : days <= 5 ? "soon" : "safe";
    element.className = `expiry-badge expiry-${tone}`;
    element.textContent = daysLabel(days);
    element.setAttribute("aria-label", days === 0 ? "روز سررسید: امروز" : `${daysLabel(days)} تا سررسید`);
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

  function renderRows() {
    const fragment = document.createDocumentFragment();
    for (const row of snapshot?.items || []) {
      const card = document.createElement("li");
      card.className = "expiry-card";
      const heading = document.createElement("div");
      heading.className = "expiry-card-head";
      const symbol = document.createElement("strong");
      symbol.textContent = row.symbol;
      const remaining = document.createElement("span");
      setBadge(remaining, row.days_remaining);
      heading.append(symbol, remaining);
      const metrics = document.createElement("dl");
      metrics.className = "expiry-metrics";
      const rawDate = String(row.end_date);
      const date = new Date(Date.UTC(Number(rawDate.slice(0, 4)), Number(rawDate.slice(4, 6)) - 1, Number(rawDate.slice(6))));
      addMetric(metrics, "سهم پایه", row.underlying_symbol);
      addMetric(metrics, "نوع", row.option_type === "call" ? "اختیار خرید" : "اختیار فروش");
      addMetric(metrics, "قیمت اعمال", row.strike_price == null ? "—" : numberFormat.format(row.strike_price));
      addMetric(metrics, "سررسید", expiryFormat.format(date));
      card.append(heading, metrics);
      fragment.append(card);
    }
    list.replaceChildren(fragment);
  }

  function render() {
    const busy = starting || running;
    refreshButton.disabled = busy;
    refreshButton.textContent = busy ? "در حال به‌روزرسانی…" : "به‌روزرسانی سررسیدها";
    panel.setAttribute("aria-busy", String(busy));
    if (snapshot?.fetched_at) {
      document.getElementById("expiryLastUpdate").textContent = `آخرین دریافت: ${updateFormat.format(new Date(snapshot.fetched_at))}`;
      badge.classList.remove("hidden");
      if (Number.isInteger(snapshot.nearest_days)) {
        setBadge(badge, snapshot.nearest_days);
      } else {
        badge.className = "expiry-badge";
        badge.textContent = "بدون سررسید";
        badge.setAttribute("aria-label", "سررسید پیش‌رو موجود نیست");
      }
      badge.title = "بر اساس آخرین دریافت و تاریخ امروز به وقت تهران";
    } else {
      badge.className = "expiry-badge hidden";
      document.getElementById("expiryLastUpdate").textContent = "هنوز دریافت نشده";
    }
    document.getElementById("expiryCount").textContent = snapshot?.total
      ? `${numberFormat.format(snapshot.items.length)} از ${numberFormat.format(snapshot.total)} قرارداد` : "";
    document.getElementById("expiryStatus").textContent = busy
      ? "در حال دریافت سررسیدهای کل بازار…"
      : !snapshot?.fetched_at ? "ابتدا سررسیدها را به‌روزرسانی کنید"
        : snapshot.items.length ? "به ترتیب نزدیک‌ترین سررسید؛ روزهای تقویمی به وقت تهران"
          : "سررسید پیش‌رو موجود نیست";
    const warning = document.getElementById("expiryWarning");
    warning.textContent = [startErrorMessage, errorMessage, snapshot?.refresh?.last_error, snapshot?.warning].filter(Boolean).join(" ");
    warning.classList.toggle("hidden", !warning.textContent);
    renderRows();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(loadSnapshot, 3000);
  }

  async function loadSnapshot() {
    if (!active || starting) return;
    const id = ++readId;
    clearTimeout(pollTimer);
    try {
      const response = await api("/api/expiries");
      if (!active || id !== readId) return;
      running = Boolean(response.refresh?.running);
      if (running || response.refresh?.last_error || (response.fetched_at ?? null) !== (snapshot?.fetched_at ?? null)) {
        startErrorMessage = "";
      }
      snapshot = response;
      errorMessage = "";
      failures = 0;
      render();
      if (running) schedulePoll();
    } catch {
      if (!active || id !== readId) return;
      failures += 1;
      if (failures >= 3) running = false;
      errorMessage = "خواندن وضعیت سررسیدها ناموفق بود؛ دوباره تلاش کنید.";
      render();
      if (failures < 3) schedulePoll();
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
      const response = await api("/api/expiries/refresh", { method: "POST" });
      if (!active || generation !== lifecycle) {
        // An old POST can still change the server after the page is restored.
        loadSnapshot();
        return;
      }
      starting = false;
      if (response.status === "cooldown") {
        errorMessage = "بین شروع به‌روزرسانی‌ها حداقل یک دقیقه صبر کنید.";
        render();
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
  window.addEventListener("pagehide", () => {
    active = false;
    starting = false;
    lifecycle += 1;
    readId += 1;
    clearTimeout(pollTimer);
    clearInterval(dayTimer);
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
          if (snapshot?.as_of_date !== marketDate()) loadSnapshot();
        }, 60000);
      }
      loadSnapshot();
    },
  };
})();
