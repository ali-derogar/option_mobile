/** Watchlist editor. Catalogue updates never overwrite the user's draft. */
(() => {
  const basePath = window.location.pathname.replace(/\/symbols\/?$/, "");
  const date = new URLSearchParams(window.location.search).get("date");
  const returnPath = basePath + (date ? `?${new URLSearchParams({ date })}` : "");
  const state = { items: [], selected: new Set(), saved: new Set(), loaded: false, saving: false, refreshing: false, catalogStarting: false, marketRefreshing: false, saveError: "", catalogSignature: "" };
  let catalogRequestId = 0;
  let pollTimer = null;
  let stopped = false;
  const el = (id) => document.getElementById(id);
  const number = (value) => value == null ? "—" : value.toLocaleString("fa-IR", { maximumFractionDigits: 0 });
  const persianDate = (value) => {
    const normalized = String(value ?? "").trim()
      .replace(/[۰-۹٠-٩]/g, (digit) => String("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩".indexOf(digit) % 10));
    const match = normalized.match(/^(\d{4})-?(\d{2})-?(\d{2})$/);
    if (!match) return "نامشخص";
    const [year, month, day] = match.slice(1).map(Number);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return "نامشخص";
    return new Intl.DateTimeFormat("fa-IR-u-ca-persian", { dateStyle: "long", timeZone: "UTC" }).format(parsed);
  };
  const normalize = (value) => String(value || "").replace(/ي/g, "ی").replace(/ك/g, "ک").replace(/[\u200c\u064b-\u065f\s]/g, "").toLowerCase();
  const dirty = () => state.selected.size !== state.saved.size || [...state.selected].some((code) => !state.saved.has(code));
  const selectionDisabled = (row) => state.saving || (!row.available && !state.saved.has(row.ins_code) && !state.selected.has(row.ins_code));

  function message(id, text, error = false) {
    el(id).textContent = text;
    el(id).classList.toggle("symbols-error", error);
  }

  function updateControls() {
    el("saveSymbols").disabled = !state.loaded || state.saving || state.marketRefreshing || !dirty();
    el("fetchCatalog").disabled = state.refreshing || state.catalogStarting || !state.loaded;
    el("fetchCatalog").textContent = state.refreshing || state.catalogStarting ? "در حال دریافت نمادها..." : "دریافت همه نمادها";
    el("selectedCount").textContent = `${number(state.selected.size)} نماد انتخاب‌شده`;
    message("saveStatus", state.marketRefreshing ? "به‌روزرسانی داده در حال انجام است؛ پس از اتمام می‌توانید ذخیره کنید."
      : state.saveError || (dirty() ? "تغییرات هنوز ذخیره نشده‌اند." : "دریافت اطلاعات تازه از صفحه اصلی انجام می‌شود."), Boolean(state.saveError));
  }

  function render() {
    const query = normalize(el("symbolSearch").value);
    const [key, direction] = el("symbolSort").value.split(":");
    const items = state.items.filter((row) => (!el("onlySelected").checked || state.selected.has(row.ins_code))
      && normalize(`${row.symbol} ${row.name || ""}`).includes(query));
    items.sort((a, b) => {
      if (a[key] == null) return b[key] == null ? 0 : 1;
      if (b[key] == null) return -1;
      const cmp = key === "symbol" ? String(a[key]).localeCompare(String(b[key]), "fa") : a[key] - b[key];
      return (direction === "asc" ? cmp : -cmp) || a.symbol.localeCompare(b.symbol, "fa");
    });
    const fragment = document.createDocumentFragment();
    for (const row of items) {
      const li = document.createElement("li");
      const label = document.createElement("label");
      label.className = "symbol-row";
      const identity = document.createElement("span");
      identity.className = "symbol-identity";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = row.ins_code;
      checkbox.checked = state.selected.has(row.ins_code);
      checkbox.disabled = selectionDisabled(row);
      checkbox.setAttribute("aria-label", `انتخاب ${row.symbol}`);
      checkbox.addEventListener("change", () => {
        state.saveError = "";
        if (checkbox.checked) state.selected.add(row.ins_code);
        else state.selected.delete(row.ins_code);
        checkbox.disabled = selectionDisabled(row);
        updateControls();
        if (el("onlySelected").checked) render();
      });
      const name = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = row.symbol;
      const subtitle = document.createElement("small");
      subtitle.textContent = row.available ? row.name || "دارایی پایه" : "در فهرست فعلی بازار موجود نیست";
      name.append(title, subtitle);
      identity.append(checkbox, name);
      label.append(identity);
      for (const [field, caption] of [["underlying_trade_volume", "حجم دارایی پایه"], ["options_trade_volume", "حجم قراردادهای اختیار"]]) {
        const volume = document.createElement("span");
        volume.className = "symbol-volume";
        const heading = document.createElement("small");
        heading.textContent = caption;
        const value = document.createElement("strong");
        value.textContent = number(row[field]);
        volume.append(heading, value);
        if (field === "underlying_trade_volume") {
          const marketDate = document.createElement("small");
          marketDate.textContent = `تاریخ معامله: ${row.market_date ? persianDate(row.market_date) : "نامشخص"}`;
          volume.append(marketDate);
        }
        label.append(volume);
      }
      li.append(label);
      fragment.append(li);
    }
    el("symbolList").replaceChildren(fragment);
    el("symbolCount").textContent = `${number(items.length)} نماد`;
    el("catalogEmpty").classList.toggle("hidden", items.length > 0);
    el("catalogEmpty").textContent = state.items.length ? "نمادی با این فیلترها پیدا نشد."
      : "برای مشاهده نمادها و حجم معاملات، «دریافت همه نمادها» را بزنید.";
    updateControls();
  }

  async function loadCatalog() {
    const requestId = ++catalogRequestId;
    let catalog, refresh;
    try {
      [catalog, refresh] = await Promise.all([api("/api/underlyings/catalog"), api("/api/refresh/status")]);
    } catch (error) {
      if (requestId !== catalogRequestId || stopped) return;
      throw error;
    }
    if (requestId !== catalogRequestId || stopped) return;
    const items = catalog.items || [];
    const signature = JSON.stringify(items);
    const changed = signature !== state.catalogSignature;
    state.items = items;
    state.catalogSignature = signature;
    state.refreshing = Boolean(catalog.refresh?.running);
    state.marketRefreshing = Boolean(refresh.running);
    const fetched = catalog.fetched_at ? new Date(catalog.fetched_at).toLocaleString("fa-IR", { timeZone: "Asia/Tehran" }) : "";
    message("catalogStatus", state.refreshing ? "در حال دریافت نمادها و حجم معاملات..."
      : catalog.refresh?.last_error || `${fetched ? `آخرین دریافت فهرست: ${fetched}` : "فهرست هنوز دریافت نشده است."}${catalog.refresh?.volume_failures ? " · حجم برخی دارایی‌ها در دسترس نیست." : ""}`, Boolean(catalog.refresh?.last_error));
    if (changed) render();
    else updateControls();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (stopped) return;
    pollTimer = setTimeout(pollCatalog, 3000);
  }

  async function pollCatalog() {
    try { await loadCatalog(); }
    catch (error) {
      state.marketRefreshing = true; // Save only after the refresh state is known.
      updateControls();
      message("catalogStatus", error.message || "خطا در دریافت وضعیت؛ در حال تلاش دوباره...", true);
    }
    schedulePoll();
  }

  async function init() {
    try {
      if (!await ensureActivation()) return;
      const watchlist = await api("/api/watchlist");
      state.saved = new Set(watchlist.ins_codes || []);
      state.selected = new Set(state.saved);
      await loadCatalog();
      state.loaded = true;
      updateControls();
      schedulePoll();
    } catch (error) {
      message("catalogStatus", `${error.message}؛ صفحه را دوباره بارگذاری کنید.`, true);
    }
  }

  el("fetchCatalog").addEventListener("click", async () => {
    catalogRequestId += 1;
    state.catalogStarting = true;
    state.refreshing = true;
    updateControls();
    try {
      await api("/api/underlyings/catalog/refresh", { method: "POST" });
      await loadCatalog();
    } catch (error) {
      state.refreshing = false;
      updateControls();
      message("catalogStatus", error.message, true);
    } finally {
      state.catalogStarting = false;
      updateControls();
    }
  });
  el("saveSymbols").addEventListener("click", async () => {
    state.saving = true;
    state.saveError = "";
    render();
    try {
      await api("/api/watchlist", { method: "PUT", body: JSON.stringify({ ins_codes: [...state.selected] }) });
      state.saved = new Set(state.selected);
      window.location.assign(returnPath);
    } catch (error) {
      if (error.status === 409) state.marketRefreshing = true;
      state.saveError = error.message;
    } finally {
      state.saving = false;
      render();
    }
  });
  el("cancelSymbols").href = returnPath;
  el("symbolSearch").addEventListener("input", render);
  el("symbolSort").addEventListener("change", render);
  el("onlySelected").addEventListener("change", render);
  function syncTheme() {
    const dark = document.documentElement.dataset.theme === "dark";
    el("themeToggle").setAttribute("aria-pressed", String(dark));
    el("themeColorMeta").content = dark ? "#0b1220" : "#f7f8fb";
  }
  el("themeToggle").addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("options-theme", theme); } catch { /* Webviews may block storage. */ }
    syncTheme();
  });
  window.addEventListener("pagehide", () => {
    stopped = true;
    catalogRequestId += 1;
    clearTimeout(pollTimer);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(pollTimer);
    else if (!stopped && state.loaded) pollCatalog();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    stopped = false;
    state.marketRefreshing = true;
    updateControls();
    pollCatalog();
  });
  bindActivation(init);
  syncTheme();
  init();
})();
