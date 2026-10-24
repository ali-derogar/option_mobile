/** Whole-market option sentiment. Its filters never read the contract table's filters. */
window.OptionSentiment = (() => {
  const oiModes = [
    { key: "buy_call", side: "call", label: "خرید Call", color: "accent" },
    { key: "buy_put", side: "put", label: "خرید Put", color: "purple" },
    { key: "sell_call", side: "call", label: "فروش Call", color: "amber" },
    { key: "sell_put", side: "put", label: "فروش Put", color: "red" },
  ];
  const model = {
    root: null, key: null, expiry: "all", date: "", contextDate: "", data: null,
    accepted: null, request: 0, timer: null, charts: [], chartScope: null, attempted: new Set(), busy: null,
    controllers: new Set(), readFailures: 0, startFailure: null, pendingRefresh: null,
    oiModes: oiModes.map((mode) => mode.key),
  };
  const sides = [{ key: "call", label: "اختیار خرید" }, { key: "put", label: "اختیار فروش" }];
  const target = () => model.date || "latest";
  const element = (id) => model.root?.querySelector(`#${id}`);
  const finite = (value) => value != null && typeof value !== "boolean" && Number.isFinite(Number(value));
  const signed = (value) => finite(value) ? `${Number(value) > 0 ? "+" : ""}${fmtNum(value)}` : "—";
  const text = (id, value) => { const el = element(id); if (el) el.textContent = value; };

  async function requestApi(path, options = {}) {
    const controller = new AbortController();
    model.controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      return await api(path, { ...options, signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted) throw new Error("مهلت دریافت پاسخ تمام شد؛ داده قبلی حفظ شده است.");
      throw error;
    } finally {
      clearTimeout(timer);
      model.controllers.delete(controller);
    }
  }

  function destroyCharts(oiOnly = false) {
    model.charts = model.charts.filter((chart) => {
      if (oiOnly && chart.canvas.id !== "sentimentOiChart") return true;
      chart.destroy();
      return false;
    });
  }

  function unmount() {
    clearTimeout(model.timer);
    model.request += 1;
    model.controllers.forEach((controller) => controller.abort());
    model.busy = null;
    model.pendingRefresh = null;
    if (model.accepted) Object.assign(model, model.accepted);
    destroyCharts();
    model.chartScope = null;
    model.root = null;
  }

  function status(message, error = false) {
    const el = element("sentimentStatus");
    if (!el) return;
    el.textContent = message;
    el.classList.toggle("sentiment-error", error);
  }

  function schedule(delay = 3000, refresh = false) {
    clearTimeout(model.timer);
    model.timer = setTimeout(() => load(refresh, true), delay);
  }

  function shell() {
    model.root.innerHTML = `
      <div class="sentiment-filters" aria-label="فیلترهای مشترک سنتیمنت">
        <label>دارایی پایه<select id="sentimentUnderlying" aria-label="دارایی پایه سنتیمنت"></select></label>
        <label>سررسید<select id="sentimentExpiry" aria-label="سررسید سنتیمنت"><option value="all">همه سررسیدها</option></select></label>
        <label>تاریخ داده<select id="sentimentDate" aria-label="تاریخ داده سنتیمنت"><option value="">آخرین داده</option></select></label>
        <button type="button" class="btn btn-primary" id="sentimentRefresh">به‌روزرسانی سنتیمنت</button>
      </div>
      <p class="sentiment-status" id="sentimentStatus" role="status" aria-live="polite"></p>
      <p class="sentiment-meta" id="sentimentMeta"></p>
      <p class="sentiment-warning hidden" id="sentimentWarning"></p>
      <div class="sentiment-market-summaries" id="sentimentSummaries"></div>
      <section class="sentiment-chart-panel" aria-labelledby="sentimentOiTitle">
        <h3 id="sentimentOiTitle">موقعیت باز بر اساس قیمت اعمال</h3>
        <p class="sentiment-price-label"></p>
        <fieldset class="sentiment-oi-modes" aria-describedby="sentimentOiModeHint">
          <legend>حالت بررسی</legend>
          <div class="sentiment-oi-mode-grid">${oiModes.map((mode) => `
            <label class="sentiment-oi-mode sentiment-position-${mode.color}">
              <input type="checkbox" value="${mode.key}" data-oi-mode aria-label="${mode.label}" ${model.oiModes.includes(mode.key) ? "checked" : ""}>
              <span>${mode.label}</span>
            </label>`).join("")}
          </div>
          <p class="sentiment-oi-mode-hint" id="sentimentOiModeHint">یک یا چند حالت را برای بررسی ترکیبی انتخاب کنید.</p>
          <p class="sentiment-oi-mode-selection" id="sentimentOiModeSelection" role="status" aria-live="polite"></p>
        </fieldset>
        <p class="sentiment-oi-mode-hint">هر موقعیت باز یک سمت خرید و یک سمت فروش دارد؛ مقادیر دو سمت برابرند و در نمایش ترکیبی با هم جمع نمی‌شوند.</p>
        <p class="sentiment-coverage" id="sentimentOiCoverage"></p>
        <div class="sentiment-legend" id="sentimentOiLegend"></div>
        <p class="sentiment-chart-empty" id="sentimentOiEmpty"></p>
        <div class="sentiment-chart-stage"><div class="sentiment-chart-scroll" tabindex="0" aria-label="نمودار موقعیت باز؛ پیمایش افقی برای همه قیمت‌های اعمال">
          <div class="sentiment-chart-wrap"><canvas id="sentimentOiChart" role="img" aria-label="موقعیت باز خرید و فروش Call و Put بر اساس قیمت اعمال"></canvas></div>
        </div><div class="sentiment-y-axis" id="sentimentOiAxis" aria-hidden="true"></div></div>
        <p class="sentiment-x-label">قیمت اعمال (ریال)</p>
        <p class="sentiment-peaks" id="sentimentPeaks"></p>
      </section>
      <section class="sentiment-chart-panel" aria-labelledby="sentimentChangeTitle">
        <h3 id="sentimentChangeTitle">تغییر موقعیت باز بر اساس قیمت اعمال</h3>
        <p class="sentiment-price-label"></p>
        <p class="sentiment-coverage" id="sentimentChangeCoverage"></p>
        <div class="sentiment-legend"><span class="sentiment-call">اختیار خرید</span><span class="sentiment-put">اختیار فروش</span><span>بالای صفر: افزایش · پایین صفر: کاهش</span></div>
        <p class="sentiment-chart-empty" id="sentimentChangeEmpty"></p>
        <div class="sentiment-chart-stage"><div class="sentiment-chart-scroll" tabindex="0" aria-label="نمودار تغییر موقعیت باز؛ پیمایش افقی برای همه قیمت‌های اعمال">
          <div class="sentiment-chart-wrap"><canvas id="sentimentChangeChart" role="img" aria-label="تغییر موقعیت باز نسبت به روز معاملاتی قبل"></canvas></div>
        </div><div class="sentiment-y-axis" id="sentimentChangeAxis" aria-hidden="true"></div></div>
        <p class="sentiment-x-label">قیمت اعمال (ریال)</p>
      </section>`;
    element("sentimentUnderlying").addEventListener("change", (event) => { model.key = event.target.value; load(); });
    element("sentimentExpiry").addEventListener("change", (event) => { model.expiry = event.target.value; load(); });
    element("sentimentDate").addEventListener("change", (event) => { model.date = event.target.value; load(); });
    element("sentimentRefresh").addEventListener("click", refresh);
    model.root.querySelectorAll("[data-oi-mode]").forEach((control) => {
      control.addEventListener("change", () => {
        model.oiModes = [...model.root.querySelectorAll("[data-oi-mode]:checked")].map((input) => input.value);
        drawCharts(true);
      });
    });
  }

  function options(id, rows, value) {
    const select = element(id);
    select.innerHTML = rows.map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join("");
    select.value = value;
  }

  function coverage(info) {
    if (!info || info.complete) return "";
    if (!info.total) return "داده موجود نیست.";
    if (info.known === info.total) return "پوشش بازار ناقص است.";
    return `داده ناقص: ${fmtNum(info.known)} از ${fmtNum(info.total)} قرارداد`;
  }

  function money(value, divisor, unit) {
    if (!finite(value)) return "—";
    const converted = Number(value) / 10 / divisor;
    const formatted = converted > 0 && converted < 0.01 ? "کمتر از ۰٫۰۱" : converted.toLocaleString("fa-IR", { maximumFractionDigits: 2 });
    return `${formatted} ${unit} تومان`;
  }

  function summaryHtml(key, title, summary, subtitle) {
    const billion = finite(summary.total.value) && summary.total.value / 10 >= 1_000_000_000;
    const divisor = billion ? 1_000_000_000 : 1_000_000;
    const unit = billion ? "میلیارد" : "میلیون";
    const fields = [["call", "ارزش معاملات اختیار خرید"], ["put", "ارزش معاملات اختیار فروش"], ["total", "مجموع ارزش معاملات"]];
    const ratio = summary.ratio_state === "infinite" ? "∞" : fmtRatio(summary.call_put_ratio);
    return `<section class="sentiment-value-block" data-sentiment-summary="${key}">
      <h3>${escapeHtml(title)}</h3><p class="sentiment-value-scope">${escapeHtml(subtitle)}</p>
      <div class="sentiment-value-grid">${fields.map(([field, label]) => {
        const metric = summary[field];
        const precise = finite(metric.value) ? `${fmtNum(metric.value)} ریال · ${Number(metric.value / 10).toLocaleString("fa-IR", { maximumFractionDigits: 1 })} تومان` : "داده موجود نیست";
        return `<div class="sentiment-value sentiment-value-${field}"><span>${label}</span>
          <strong tabindex="0" data-exact-value="${escapeHtml(precise)}" aria-label="${escapeHtml(`${label}: ${precise}`)}">${money(metric.value, divisor, unit)}</strong>
          <small>${escapeHtml(coverage(metric))}</small></div>`;
      }).join("")}
      <div class="sentiment-value"><span>نسبت ارزش اختیار خرید به اختیار فروش</span><strong dir="ltr">${ratio}</strong></div></div>
    </section>`;
  }

  function render(data) {
    const assets = [...data.underlyings];
    if (model.key && !assets.some((a) => a.key === model.key)) assets.unshift({ key: model.key, symbol: model.key });
    options("sentimentUnderlying", assets.map((a) => [a.key, a.symbol]), model.key || "");
    options("sentimentExpiry", [["all", "همه سررسیدها"], ...data.expiries.map((d) => [d, fmtDate(d)])], model.expiry);
    const dates = [...new Set([...data.dates, model.contextDate, model.date].filter(Boolean))].sort().reverse();
    options("sentimentDate", [["", "آخرین داده"], ...dates.map((d) => [d, fmtDate(d)])], model.date);
    const assetName = assets.find((a) => a.key === model.key)?.symbol || "دارایی انتخاب‌شده";
    const dateLabel = data.data_date ? fmtDate(data.data_date) : "—";
    const received = data.received_at ? new Date(data.received_at).toLocaleString("fa-IR", { timeZone: "Asia/Tehran" }) : "—";
    text("sentimentMeta", `تاریخ معاملاتی: ${dateLabel} · زمان دریافت به وقت تهران: ${received}`);
    const messages = [data.warning];
    if (data.quality.unknown_type_count) messages.push(`${fmtNum(data.quality.unknown_type_count)} قرارداد با نوع اختیار نامشخص کنار گذاشته شده است.`);
    if (data.quality.invalid_strike_count) messages.push(`${fmtNum(data.quality.invalid_strike_count)} قرارداد قیمت اعمال معتبر ندارد.`);
    const warning = element("sentimentWarning");
    warning.textContent = messages.filter(Boolean).join(" ");
    warning.classList.toggle("hidden", !warning.textContent);
    element("sentimentSummaries").innerHTML = summaryHtml("market", "کل بازار", data.summaries.market, `${dateLabel} · همه دارایی‌ها و همه سررسیدها`)
      + summaryHtml("underlying", "دارایی پایه", data.summaries.underlying, `${assetName} · همه سررسیدها`)
      + (model.expiry !== "all" ? summaryHtml("selection", "انتخاب فعلی", data.summaries.selection, `${assetName} · ${fmtDate(model.expiry)}`) : "");
    const priceText = `قیمت دارایی پایه${data.spot_price_kind === "closing" ? " (پایانی)" : ""}: ${fmtNum(data.spot_price)} ریال · نزدیک‌ترین قیمت اعمال (ATM): ${fmtNum(data.atm_strike)} ریال`;
    model.root.querySelectorAll(".sentiment-price-label").forEach((el) => { el.textContent = priceText; });
    text("sentimentChangeCoverage", coverage(data.quality.change));
    drawCharts();
  }

  function renderOiModes(selectedModes) {
    const data = model.data;
    model.root.querySelectorAll("[data-oi-mode]").forEach((control) => {
      control.checked = model.oiModes.includes(control.value);
    });
    text("sentimentOiModeSelection", selectedModes.length ? `نمایش: ${selectedModes.map((mode) => mode.label).join(" + ")}` : "هیچ حالتی انتخاب نشده است.");
    element("sentimentOiLegend").innerHTML = selectedModes.map((mode) => `<span class="sentiment-position-legend sentiment-position-${mode.color}">${mode.label}</span>`).join("") + "<span>واحد: تعداد قرارداد</span>";
    const selectedSides = [...new Set(selectedModes.map((mode) => mode.side))];
    const info = data.strikes.reduce((result, row) => {
      selectedSides.forEach((side) => {
        const metric = row.coverage[`${side}_oi`];
        result.known += metric.known;
        result.total += metric.total;
      });
      return result;
    }, { known: 0, total: 0 });
    info.complete = info.known === info.total;
    text("sentimentOiCoverage", selectedSides.length === 2 ? coverage(data.quality.oi) : selectedSides.length ? coverage(info) : "");
    text("sentimentPeaks", selectedModes.map((mode) => {
      const peak = data.peaks[mode.side];
      const label = `بیشترین موقعیت باز ${mode.label}`;
      return peak ? `${label}: ${fmtNum(peak.value)} قرارداد در قیمت اعمال ${fmtNum(peak.strike_price)} ریال${peak.complete && peak.scope_complete !== false ? "" : " (داده ناقص؛ بیشینه در داده‌های موجود)"}` : `${label}: —`;
    }).join(" · "));
  }

  const priceMarkers = {
    id: "sentimentPriceMarkers",
    afterLayout(chart) {
      const axis = element(chart.canvas.id.replace("Chart", "Axis"));
      if (!axis) return;
      axis.replaceChildren(...chart.scales.y.ticks.map((tick, i) => {
        const label = document.createElement("span");
        label.textContent = fmtNum(tick.value);
        label.style.top = `${chart.scales.y.getPixelForTick(i)}px`;
        return label;
      }));
    },
    afterDatasetsDraw(chart) {
      const data = model.data;
      if (!data) return;
      const { ctx, chartArea: area, scales: { x } } = chart;
      const palette = chartPalette();
      for (const [value, label, color, level] of [
        [data.spot_price, "قیمت پایه", palette.red, 0],
        [data.atm_strike, "ATM", palette.purple, 1],
      ]) {
        if (!finite(value)) continue;
        const pixel = x.getPixelForValue(Number(value));
        if (pixel < area.left || pixel > area.right) continue;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.setLineDash(level ? [3, 4] : [7, 3]);
        ctx.beginPath(); ctx.moveTo(pixel, area.top); ctx.lineTo(pixel, area.bottom); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.font = "12px Vazirmatn, sans-serif";
        const content = `${label}: ${fmtNum(value)}`;
        const width = ctx.measureText(content).width;
        const labelX = Math.max(area.left + width / 2, Math.min(area.right - width / 2, pixel));
        ctx.textAlign = "center";
        ctx.fillText(content, labelX, area.top - 28 + level * 17);
        ctx.restore();
      }
    },
  };

  function drawCharts(oiOnly = false) {
    const previousCharts = new Set(model.charts.map((chart) => chart.canvas.id));
    const positions = ["Oi", "Change"].map((name) => element(`sentiment${name}Chart`)?.parentElement.parentElement.scrollLeft || 0);
    destroyCharts(oiOnly);
    if (!model.root || !model.data) return;
    const data = model.data;
    const selectedModes = oiModes.filter((mode) => model.oiModes.includes(mode.key));
    renderOiModes(selectedModes);
    model.chartScope ||= {};
    for (const [chartIndex, [suffix, field]] of [["Oi", "oi"], ["Change", "change"]].entries()) {
      if (oiOnly && field !== "oi") continue;
      const rows = data.strikes;
      const series = field === "oi" ? selectedModes : sides.map((side) => ({ ...side, side: side.key, color: side.key === "put" ? "purple" : "accent" }));
      const scope = `${data.underlying_key}|${data.expiry}|${data.data_date}|${field === "oi" ? model.oiModes.join(",") : ""}`;
      const sameScope = scope === model.chartScope[suffix];
      model.chartScope[suffix] = scope;
      const canvas = element(`sentiment${suffix}Chart`);
      const wrap = canvas.parentElement;
      const available = rows.some((row) => series.some((mode) => row.coverage[`${mode.side}_${field}`].known > 0));
      wrap.parentElement.parentElement.classList.toggle("hidden", !available);
      const allZero = available && rows.every((row) => series.every((mode) => row[`${mode.side}_${field}`] == null || row[`${mode.side}_${field}`] === 0));
      text(`sentiment${suffix}Empty`, available ? (allZero ? `${field === "oi" ? "موقعیت باز" : "تغییر موقعیت باز"} قراردادهای دارای داده صفر است.` : "")
        : field === "oi" ? (!selectedModes.length ? "برای نمایش نمودار، حداقل یکی از چهار حالت را انتخاب کنید." : "داده قابل نمایش موقعیت باز برای قیمت‌های اعمال این انتخاب موجود نیست.") : "داده قابل نمایش برای مقایسه با روز معاملاتی قبل موجود نیست.");
      if (!available || typeof Chart === "undefined") continue;
      wrap.style.width = `${Math.max(wrap.parentElement.clientWidth, rows.length * Math.max(56, series.length * 24 + 16), 320)}px`;
      const palette = chartPalette();
      const strikes = rows.map((r) => r.strike_price);
      const spacing = Math.min(...strikes.slice(1).map((v, i) => v - strikes[i])) || strikes[0] * 0.05;
      const padding = Number.isFinite(spacing) ? spacing : Math.max(1, strikes[0] * 0.05);
      const bounds = [...strikes, ...(finite(data.spot_price) ? [data.spot_price] : [])];
      model.charts.push(new Chart(canvas, {
        type: "bar", plugins: [priceMarkers],
        data: { datasets: series.map((mode) => ({
          label: mode.label, backgroundColor: palette[mode.color],
          borderColor: palette[mode.color], borderWidth: 1, maxBarThickness: 22,
          data: rows.map((row) => ({ x: row.strike_price, y: row[`${mode.side}_${field}`] })),
        })) },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
          layout: { padding: { top: 45 } },
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: { rtl: true, textDirection: "rtl", titleFont: { family: "Vazirmatn" }, bodyFont: { family: "Vazirmatn" }, callbacks: {
              title: (items) => `قیمت اعمال: ${fmtNum(items[0]?.parsed.x)} ریال`,
              label: (item) => {
                const side = series[item.datasetIndex].side;
                const info = rows[item.dataIndex].coverage[`${side}_${field}`];
                return `${item.dataset.label}: ${field === "change" ? signed(item.parsed.y) : fmtNum(item.parsed.y)} قرارداد${info.complete ? "" : ` · ${coverage(info)}`}`;
              },
            } },
          },
          scales: {
            x: { type: "linear", min: Math.min(...bounds) - padding, max: Math.max(...bounds) + padding,
              afterBuildTicks: (axis) => { axis.ticks = strikes.map((value) => ({ value })); },
              ticks: { color: palette.muted, callback: (v) => fmtNum(v), maxRotation: 55, minRotation: 35, font: { family: "Vazirmatn" } }, grid: { display: false } },
            y: { beginAtZero: true, afterFit: (axis) => { axis.width = 52; }, ticks: { color: "transparent", callback: (v) => fmtNum(v), font: { family: "Vazirmatn" }, precision: 0 },
              grid: { color: (context) => context.tick?.value === 0 ? palette.text : palette.grid, lineWidth: (context) => context.tick?.value === 0 ? 2 : 1 } },
          },
        },
      }));
      const chart = model.charts[model.charts.length - 1];
      const scroller = wrap.parentElement;
      scroller.scrollLeft = sameScope && previousCharts.has(canvas.id) ? positions[chartIndex]
        : finite(data.spot_price) ? Math.max(0, chart.scales.x.getPixelForValue(Number(data.spot_price)) - scroller.clientWidth / 2) : 0;
    }
  }

  async function load(shouldRefresh = false, polling = false) {
    if (!model.root) return;
    clearTimeout(model.timer);
    if (model.pendingRefresh?.target !== target()) model.pendingRefresh = null;
    if (shouldRefresh) model.pendingRefresh = { target: target(), initialized: false };
    const request = ++model.request;
    const params = new URLSearchParams({ expiry: model.expiry });
    if (model.key) params.set("underlying_key", model.key);
    if (model.date) params.set("date", model.date);
    status("در حال دریافت تحلیل...");
    try {
      const data = await requestApi(`/api/option-sentiment?${params}`);
      if (!model.root || request !== model.request) return;
      model.readFailures = 0;
      model.key = data.underlying_key || model.key;
      model.expiry = data.expiry;
      model.data = data;
      model.accepted = { key: model.key, expiry: model.expiry, date: model.date };
      render(data);
      const running = Boolean(data.refresh?.running);
      const sameTarget = (data.refresh?.target_date || "") === model.date;
      const refreshError = sameTarget ? data.refresh?.last_error : null;
      const pending = model.pendingRefresh;
      if (pending && !pending.initialized) {
        pending.receivedAt = data.received_at;
        pending.initialized = true;
      }
      if (pending && ((running && sameTarget) || (data.cached && data.received_at !== pending.receivedAt))) model.pendingRefresh = null;
      if (model.startFailure?.target === target()
          && ((running && (data.refresh?.target_date || "") === model.date)
            || (data.cached && data.received_at !== model.startFailure.receivedAt))) model.startFailure = null;
      const startError = model.startFailure?.target === target() ? model.startFailure.message : null;
      element("sentimentRefresh").disabled = running;
      status(refreshError || startError || (running ? "در حال به‌روزرسانی کل بازار؛ آخرین داده موفق نمایش داده می‌شود." : data.cached ? "تحلیل آماده است." : "در انتظار دریافت داده کامل بازار..."), Boolean(refreshError || startError));
      if (running) schedule();
      else if (model.pendingRefresh || (!data.cached && !refreshError && !model.attempted.has(target()))) await refresh();
    } catch (error) {
      if (!model.root || request !== model.request) return;
      if (model.accepted) {
        Object.assign(model, model.accepted);
        render(model.data);
      }
      element("sentimentRefresh").disabled = false;
      status(error.message || "دریافت تحلیل ناموفق بود؛ داده قبلی نمایش داده می‌شود.", true);
      if (polling && ++model.readFailures < 3) schedule();
    }
  }

  async function refresh() {
    if (!model.root || model.busy === model.request) return;
    clearTimeout(model.timer);
    model.readFailures = 0;
    const request = model.request;
    model.busy = request;
    model.pendingRefresh = { target: target(), receivedAt: model.data?.received_at, initialized: true };
    model.startFailure = null;
    model.attempted.add(target());
    element("sentimentRefresh").disabled = true;
    status("در حال درخواست به‌روزرسانی...");
    try {
      const result = await requestApi("/api/option-sentiment/refresh", { method: "POST", body: JSON.stringify({ date: model.date || null }) });
      if (!model.root || request !== model.request) return;
      if (result.status === "cached") {
        model.pendingRefresh = null;
        await load();
      }
      else if (result.status === "cooldown") {
        status(`دریافت تازه پس از ${fmtNum(result.retry_after || 60)} ثانیه انجام می‌شود؛ داده قبلی نمایش داده می‌شود.`);
        schedule((result.retry_after || 60) * 1000, true);
      } else {
        if (result.status === "started") model.pendingRefresh = null;
        status("در حال به‌روزرسانی کل بازار؛ آخرین داده موفق نمایش داده می‌شود.");
        schedule();
      }
    } catch (error) {
      if (!model.root || request !== model.request) return;
      element("sentimentRefresh").disabled = false;
      const message = error.message || "شروع به‌روزرسانی ناموفق بود.";
      model.pendingRefresh = null;
      model.startFailure = { target: target(), receivedAt: model.data?.received_at, message };
      status(message, true);
      // A missing start response may still have launched the server job.
      // Reconcile through GET instead of sending a duplicate POST.
      schedule();
    } finally {
      if (model.busy === request) model.busy = null;
    }
  }

  function mount(root, key, contextDate) {
    if (model.root === root) return;
    unmount();
    model.root = root;
    model.key = model.key || String(key || "");
    model.contextDate = contextDate || "";
    model.attempted.clear();
    model.readFailures = 0;
    shell();
    if (model.data) render(model.data);
    load(!model.date);
  }

  window.addEventListener("pagehide", unmount);
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || model.root) return;
    const root = document.getElementById("optionSentimentRoot");
    if (root && document.body.classList.contains("option-sentiment-active")) {
      mount(root, model.key, model.contextDate);
    }
  });
  return { mount, unmount, refresh, redraw: drawCharts };
})();
