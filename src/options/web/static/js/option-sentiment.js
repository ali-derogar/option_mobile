/** Whole-market option sentiment. Its filters never read the contract table's filters. */
window.OptionSentiment = (() => {
  // Show each contract type once, matching the feed's call/put OI counts.
  const oiModes = [
    { key: "call", side: "call", label: "کال", color: "accent" },
    { key: "put", side: "put", label: "پوت", color: "purple" },
  ];
  const model = {
    root: null, key: null, expiry: "all", date: "", contextDate: "", data: null,
    accepted: null, request: 0, timer: null, charts: [], chartScope: null, attempted: new Set(), busy: null,
    controllers: new Set(), readFailures: 0, startFailure: null, pendingRefresh: null,
    oiModes: oiModes.map((mode) => mode.key),
    flowAudience: "natural", flowMeasure: "volume", flowData: null, flowScope: null, flowRequest: 0,
    flowController: null, flowLoading: false, flowError: false, flowOutdated: false, flowCache: new Map(), heatmapType: "total",
  };
  const sides = [{ key: "call", label: "کال" }, { key: "put", label: "پوت" }];
  const target = () => model.date || "latest";
  const element = (id) => model.root?.querySelector(`#${id}`);
  const finite = (value) => value != null && typeof value !== "boolean" && Number.isFinite(Number(value));
  const signed = (value) => finite(value) ? `${Number(value) > 0 ? "+" : ""}${fmtNum(value)}` : "—";
  const text = (id, value) => { const el = element(id); if (el) el.textContent = value; };
  const compactCount = (value) => Number(value).toLocaleString("fa-IR", { notation: "compact", maximumFractionDigits: 1 });

  function centerChart(chart) {
    if (!chart || !finite(model.data?.spot_price)) return;
    const scroller = chart.canvas.parentElement.parentElement;
    scroller.scrollLeft = Math.max(0, chart.scales.x.getPixelForValue(Number(model.data.spot_price)) - scroller.clientWidth / 2);
  }

  function chartFooter(suffix) {
    return `<div class="sentiment-chart-footer" id="sentiment${suffix}Footer">
      <span class="sentiment-x-label">قیمت اعمال (ریال)</span>
      <button type="button" class="sentiment-center-chart hidden" data-center-chart="sentiment${suffix}Chart" aria-label="بازگشت به قیمت پایه">قیمت پایه</button>
      <span class="sentiment-chart-hint sr-only" id="sentiment${suffix}Hint">برای دیدن مقدار دقیق، ستون را لمس کنید.</span>
    </div>`;
  }

  async function requestApi(path, options = {}, timeout = 15000, controller = new AbortController()) {
    model.controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), timeout);
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

  function destroyCharts(only = null) {
    model.charts = model.charts.filter((chart) => {
      if (only && chart.canvas.id !== `sentiment${only}Chart`) return true;
      chart.destroy();
      return false;
    });
  }

  function unmount() {
    clearTimeout(model.timer);
    model.request += 1;
    model.flowRequest += 1;
    model.flowScope = null;
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
    el.classList.toggle("sr-only", !error && message === "تحلیل آماده است.");
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
        <button type="button" class="btn btn-primary" id="sentimentRefresh">به‌روزرسانی</button>
      </div>
      <p class="sentiment-status" id="sentimentStatus" role="status" aria-live="polite"></p>
      <p class="sentiment-meta" id="sentimentMeta"></p>
      <p class="sentiment-warning hidden" id="sentimentWarning"></p>
      <div class="sentiment-market-summaries" id="sentimentSummaries"></div>
      <div class="sentiment-price-label" role="group" aria-label="قیمت‌های مرجع نمودارها"></div>
      <section class="sentiment-chart-panel" aria-labelledby="sentimentOiTitle">
        <h3 id="sentimentOiTitle">موقعیت باز بر اساس قیمت اعمال</h3>
        <fieldset class="sentiment-oi-modes">
          <legend class="sr-only">نوع قرارداد</legend>
          <div class="sentiment-oi-mode-grid">${oiModes.map((mode) => `
            <label class="sentiment-oi-mode sentiment-position-${mode.color}">
              <input type="checkbox" value="${mode.key}" data-oi-mode aria-label="${mode.label}" aria-controls="sentimentOiChart" ${model.oiModes.includes(mode.key) ? "checked" : ""}>
              <span class="sentiment-oi-mode-copy"><strong>${mode.label}</strong></span>
            </label>`).join("")}
          </div>
          <p class="sentiment-oi-mode-selection sr-only" id="sentimentOiModeSelection" role="status" aria-live="polite"></p>
        </fieldset>
        <p class="sentiment-coverage" id="sentimentOiCoverage"></p>
        <div class="sentiment-legend sr-only" id="sentimentOiLegend"></div>
        <p class="sentiment-chart-unit">تعداد قرارداد</p>
        <p class="sentiment-chart-empty" id="sentimentOiEmpty"></p>
        <div class="sentiment-chart-stage"><div class="sentiment-chart-scroll" tabindex="0" aria-label="نمودار موقعیت باز؛ پیمایش افقی برای همه قیمت‌های اعمال">
          <div class="sentiment-chart-wrap"><canvas id="sentimentOiChart" role="img" aria-label="موقعیت باز کال و پوت بر اساس قیمت اعمال"></canvas></div>
        </div><div class="sentiment-y-axis" id="sentimentOiAxis" aria-hidden="true"></div></div>
        ${chartFooter("Oi")}
        <details class="sentiment-chart-details"><summary>بیشترین موقعیت‌ها</summary>
          <div class="sentiment-peaks" id="sentimentPeaks"></div>
        </details>
      </section>
      <section class="sentiment-chart-panel" aria-labelledby="sentimentChangeTitle">
        <h3 id="sentimentChangeTitle">تغییر موقعیت باز</h3>
        <p class="sentiment-coverage" id="sentimentChangeCoverage"></p>
        <div class="sentiment-legend"><span class="sentiment-call">کال</span><span class="sentiment-put">پوت</span></div>
        <p class="sentiment-chart-unit">تغییر تعداد قرارداد</p>
        <p class="sentiment-chart-empty" id="sentimentChangeEmpty"></p>
        <div class="sentiment-chart-stage"><div class="sentiment-chart-scroll" tabindex="0" aria-label="نمودار تغییر موقعیت باز؛ پیمایش افقی برای همه قیمت‌های اعمال">
          <div class="sentiment-chart-wrap"><canvas id="sentimentChangeChart" role="img" aria-label="تغییر موقعیت باز نسبت به روز معاملاتی قبل"></canvas></div>
        </div><div class="sentiment-y-axis" id="sentimentChangeAxis" aria-hidden="true"></div></div>
        ${chartFooter("Change")}
      </section>
      <section class="sentiment-chart-panel" aria-labelledby="sentimentFlowTitle">
        <h3 id="sentimentFlowTitle">خالص خرید</h3>
        <div class="sentiment-flow-controls">
          <select id="sentimentFlowAudience" aria-label="گروه خالص خرید"><option value="natural">حقیقی‌ها</option><option value="legal">حقوقی‌ها</option></select>
          <select id="sentimentFlowMeasure" aria-label="واحد خالص خرید"><option value="volume">قرارداد</option><option value="value">تومان</option></select>
        </div>
        <p class="sentiment-coverage" id="sentimentFlowStatus" role="status" aria-live="polite"></p>
        <button type="button" class="btn hidden" id="sentimentFlowRetry">تلاش دوباره</button>
        <div class="sentiment-legend"><span class="sentiment-call">کال</span><span class="sentiment-put">پوت</span></div>
        <p class="sentiment-chart-unit" id="sentimentFlowUnit"></p>
        <p class="sentiment-chart-empty" id="sentimentFlowEmpty"></p>
        <div class="sentiment-chart-stage"><div class="sentiment-chart-scroll" tabindex="0" aria-label="نمودار خالص خرید؛ پیمایش افقی برای همه قیمت‌های اعمال">
          <div class="sentiment-chart-wrap"><canvas id="sentimentFlowChart" role="img" aria-label="خالص خرید کال و پوت بر اساس قیمت اعمال"></canvas></div>
        </div><div class="sentiment-y-axis" id="sentimentFlowAxis" aria-hidden="true"></div></div>
        ${chartFooter("Flow")}
      </section>
      <section class="sentiment-chart-panel" aria-labelledby="sentimentHeatmapTitle">
        <div class="sentiment-panel-heading">
          <h3 id="sentimentHeatmapTitle">نقشه فعالیت معاملات</h3>
          <select id="sentimentHeatmapType" aria-label="نوع قرارداد نقشه فعالیت"><option value="total">همه</option><option value="call">کال</option><option value="put">پوت</option></select>
        </div>
        <p class="sentiment-chart-unit">ارزش معاملات (تومان)</p>
        <div class="sentiment-heatmap-scroll" id="sentimentHeatmap" tabindex="0" role="region" aria-label="ارزش معاملات بر اساس قیمت اعمال و سررسید"></div>
        <div class="sentiment-heatmap-scale" aria-label="شدت فعالیت از کم به زیاد"><span>کم</span>${[0, 1, 2, 3, 4].map(level => `<i data-heat-level="${level}"></i>`).join("")}<span>زیاد</span></div>
        <p class="sentiment-heatmap-detail" id="sentimentHeatmapDetail" role="status" aria-live="polite"></p>
      </section>`;
    element("sentimentUnderlying").addEventListener("change", (event) => { model.key = event.target.value; load(); });
    element("sentimentExpiry").addEventListener("change", (event) => { model.expiry = event.target.value; load(); });
    element("sentimentDate").addEventListener("change", (event) => { model.date = event.target.value; load(); });
    element("sentimentRefresh").addEventListener("click", refresh);
    element("sentimentFlowAudience").value = model.flowAudience;
    element("sentimentFlowAudience").addEventListener("change", (event) => {
      model.flowAudience = event.target.value;
      drawCharts("Flow");
    });
    element("sentimentFlowMeasure").value = model.flowMeasure;
    element("sentimentFlowMeasure").addEventListener("change", (event) => {
      model.flowMeasure = event.target.value;
      drawCharts("Flow");
    });
    element("sentimentFlowRetry").addEventListener("click", () => model.flowOutdated ? load() : loadFlow(model.data, true));
    element("sentimentHeatmapType").value = model.heatmapType;
    element("sentimentHeatmapType").addEventListener("change", (event) => {
      model.heatmapType = event.target.value;
      renderHeatmap();
    });
    element("sentimentHeatmap").addEventListener("click", (event) => {
      const cell = event.target.closest("[data-heat-cell]");
      if (cell) selectHeatCell(cell);
    });
    model.root.querySelectorAll("[data-center-chart]").forEach((button) => {
      button.addEventListener("click", () => centerChart(model.charts.find((chart) => chart.canvas.id === button.dataset.centerChart)));
    });
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
    if (info.overflow) return "مقدار از محدوده محاسبه خارج است.";
    if (!info.total) return "داده موجود نیست.";
    if (info.known === info.total) return "پوشش بازار ناقص است.";
    return `داده ناقص: ${fmtNum(info.known)} از ${fmtNum(info.total)} قرارداد`;
  }

  function money(value, divisor) {
    if (!finite(value)) return "—";
    const converted = Number(value) / 10 / divisor;
    const formatted = converted > 0 && converted < 0.01 ? "کمتر از ۰٫۰۱" : converted.toLocaleString("fa-IR", { maximumFractionDigits: 2 });
    return formatted;
  }

  function summaryHtml(key, title, summary, subtitle) {
    const billion = finite(summary.total.value) && summary.total.value / 10 >= 1_000_000_000;
    const divisor = billion ? 1_000_000_000 : 1_000_000;
    const unit = billion ? "میلیارد" : "میلیون";
    const metricHtml = (field, label, metric) => {
      const precise = finite(metric.value) ? `${fmtNum(metric.value)} ریال · ${Number(metric.value / 10).toLocaleString("fa-IR", { maximumFractionDigits: 1 })} تومان` : "داده موجود نیست";
      return `<div class="sentiment-value sentiment-value-${field}"><span>${escapeHtml(label)}</span>
        <strong tabindex="0" data-exact-value="${escapeHtml(precise)}" aria-label="${escapeHtml(`${label}: ${precise}`)}">${money(metric.value, divisor)}</strong>
        <small>${escapeHtml(coverage(metric))}</small></div>`;
    };
    const ratio = summary.ratio_state === "infinite" ? "∞" : fmtRatio(summary.call_put_ratio);
    return `<section class="sentiment-value-block" data-sentiment-summary="${key}">
      <div class="sentiment-value-heading"><h3>${escapeHtml(title)}</h3><span class="sentiment-value-unit" aria-label="واحد ارزش معاملات">${unit} تومان</span></div>
      <p class="sentiment-value-scope">${escapeHtml(subtitle)}</p>
      <div class="sentiment-value-grid">${sides.map(({ key, label }) => metricHtml(key, `ارزش معاملات ${label}`, summary[key])).join("")}</div>
      <div class="sentiment-value-totals">
        ${metricHtml("total", "مجموع", summary.total)}
        <div class="sentiment-value sentiment-value-ratio"><span>نسبت کال به پوت</span><strong dir="ltr">${ratio}</strong></div>
      </div>
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
    const receivedTime = data.received_at ? new Date(data.received_at).toLocaleTimeString("fa-IR", { timeZone: "Asia/Tehran", hour: "2-digit", minute: "2-digit" }) : "—";
    text("sentimentMeta", `${dateLabel} · به‌روزرسانی ${receivedTime}`);
    const messages = [data.warning];
    if (data.quality.unknown_type_count) messages.push(`${fmtNum(data.quality.unknown_type_count)} قرارداد با نوع اختیار نامشخص در تفکیک Call/Put لحاظ نشده است؛ ارزش معاملات موجود آن‌ها فقط در مجموع لحاظ می‌شود.`);
    if (data.quality.invalid_strike_count) messages.push(`${fmtNum(data.quality.invalid_strike_count)} قرارداد قیمت اعمال معتبر ندارد.`);
    if (data.quality.spot_price_conflict) messages.push("قیمت‌های گزارش‌شده دارایی پایه ناسازگارند؛ نشانگر قیمت فقط در صورت وجود مقدار سازگار نمایش داده می‌شود.");
    const warning = element("sentimentWarning");
    warning.textContent = messages.filter(Boolean).join(" ");
    warning.classList.toggle("hidden", !warning.textContent);
    element("sentimentSummaries").innerHTML = summaryHtml("market", "کل بازار", data.summaries.market, "همه نمادها · همه سررسیدها")
      + summaryHtml("underlying", "دارایی پایه", data.summaries.underlying, `${assetName} · همه سررسیدها`)
      + (model.expiry !== "all" ? summaryHtml("selection", "انتخاب فعلی", data.summaries.selection, `${assetName} · ${fmtDate(model.expiry)}`) : "");
    const priceHtml = `<div class="sentiment-price-spot"><span>پایه${data.spot_price_kind === "closing" ? " (پایانی)" : ""}</span><strong>${fmtNum(data.spot_price)} <small>ریال</small></strong></div>
      <div class="sentiment-price-atm" title="نزدیک‌ترین قیمت اعمال به قیمت پایه"><span>ATM</span><strong>${fmtNum(data.atm_strike)} <small>ریال</small></strong></div>`;
    model.root.querySelectorAll(".sentiment-price-label").forEach((el) => { el.innerHTML = priceHtml; });
    text("sentimentChangeCoverage", coverage(data.quality.change));
    loadFlow(data);
    renderHeatmap();
    drawCharts();
  }

  async function loadFlow(data, retry = false) {
    if (!model.root || !data) return;
    const scope = `${data.underlying_key}|${data.expiry}|${data.data_date}|${data.received_at}`;
    if (!retry && model.flowScope === scope) return;
    const request = ++model.flowRequest;
    model.flowController?.abort();
    model.flowScope = scope;
    model.flowData = retry ? null : model.flowCache.get(scope) || null;
    model.flowError = false;
    model.flowOutdated = false;
    model.flowLoading = Boolean(data.data_date && data.underlying_key && !model.flowData);
    if (!model.flowLoading) return;
    updateFlowStatus();
    if (retry) drawCharts("Flow");
    const controller = model.flowController = new AbortController();
    const params = new URLSearchParams({ underlying_key: data.underlying_key, expiry: data.expiry, date: data.data_date });
    try {
      const result = await requestApi(`/api/option-sentiment/flow?${params}`, {}, 90000, controller);
      if (!model.root || request !== model.flowRequest) return;
      if (result.data_date !== data.data_date || result.underlying_key !== data.underlying_key || result.expiry !== data.expiry) throw new Error("scope mismatch");
      if ((result.received_at ?? null) !== (data.received_at ?? null)) {
        model.flowOutdated = true;
        throw new Error("snapshot mismatch");
      }
      model.flowData = result;
      model.flowCache.set(scope, result);
      if (model.flowCache.size > 8) model.flowCache.delete(model.flowCache.keys().next().value);
    } catch (error) {
      if (!model.root || request !== model.flowRequest) return;
      model.flowError = true;
    } finally {
      if (model.root && request === model.flowRequest) {
        model.flowLoading = false;
        drawCharts("Flow");
      }
    }
  }

  function updateFlowStatus() {
    const info = model.flowData?.audiences?.[model.flowAudience]?.[model.flowMeasure]?.coverage;
    text("sentimentFlowUnit", model.flowMeasure === "value" ? "ارزش خرید منهای فروش (تومان)" : "حجم خرید منهای فروش (قرارداد)");
    text("sentimentFlowStatus", model.flowLoading ? "در حال دریافت خرید و فروش..." : model.flowError ? "دریافت خرید و فروش ناموفق بود." : coverage(info));
    element("sentimentFlowRetry")?.classList.toggle("hidden", !model.flowError);
  }

  function selectHeatCell(cell) {
    model.root.querySelectorAll("[data-heat-cell]").forEach(el => el.setAttribute("aria-pressed", String(el === cell)));
    text("sentimentHeatmapDetail", cell.getAttribute("aria-label"));
  }

  function renderHeatmap() {
    const matrix = model.data?.activity;
    const host = element("sentimentHeatmap");
    if (!host) return;
    const cells = matrix?.date_verified === false ? [] : matrix?.cells || [];
    const type = model.heatmapType;
    const byPosition = new Map(cells.map(cell => [`${cell.strike_price}|${cell.expiry}`, cell[type]]));
    const maximum = Math.max(0, ...cells.map(cell => cell[type]?.complete && finite(cell[type].value) ? Number(cell[type].value) : 0));
    element("sentimentHeatmapDetail").textContent = "";
    host.nextElementSibling.classList.toggle("hidden", !cells.length);
    if (!cells.length) {
      host.innerHTML = `<p class="sentiment-chart-empty">${matrix?.date_verified === false ? "در انتظار دریافت داده به‌روز بازار..." : "داده فعالیت معاملات برای این انتخاب موجود نیست."}</p>`;
      return;
    }
    host.innerHTML = `<table class="sentiment-heatmap-table"><caption class="sr-only">ارزش معاملات؛ سطرها قیمت اعمال به ریال و ستون‌ها سررسید</caption>
      <thead><tr><th scope="col">اعمال (ریال)</th>${matrix.expiries.map(expiry => `<th scope="col">${escapeHtml(fmtDate(expiry))}</th>`).join("")}</tr></thead>
      <tbody>${matrix.strikes.map(strike => `<tr><th scope="row">${fmtNum(strike)}</th>${matrix.expiries.map(expiry => {
        const info = byPosition.get(`${strike}|${expiry}`);
        const known = info?.complete && finite(info.value);
        const value = known ? Number(info.value) : null;
        const level = known && value > 0 ? Math.max(1, Math.ceil(Math.sqrt(value / maximum) * 4)) : 0;
        const detail = `اعمال ${fmtNum(strike)} ریال · ${fmtDate(expiry)} · ${known ? `${(value / 10).toLocaleString("fa-IR", { maximumFractionDigits: 1 })} تومان` : info ? "داده ناقص" : "قراردادی موجود نیست"}`;
        return `<td><button type="button" data-heat-cell data-heat-level="${level}" aria-pressed="false" aria-label="${escapeHtml(detail)}" ${!info ? "disabled" : ""}>${known ? compactCount(value / 10) : "—"}</button></td>`;
      }).join("")}</tr>`).join("")}</tbody></table>`;
  }

  function renderOiModes(selectedModes) {
    const data = model.data;
    model.root.querySelectorAll("[data-oi-mode]").forEach((control) => {
      control.checked = model.oiModes.includes(control.value);
    });
    text("sentimentOiModeSelection", selectedModes.length ? `نمایش: ${selectedModes.map((mode) => mode.label).join(" + ")}` : "هیچ حالتی انتخاب نشده است.");
    element("sentimentOiLegend").innerHTML = selectedModes.map((mode) => `<span class="sentiment-position-legend sentiment-position-${mode.color}">${mode.label}</span>`).join("");
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
    element("sentimentPeaks").innerHTML = selectedModes.map((mode) => {
      const peak = data.peaks[mode.side];
      return `<div><span class="sentiment-position-${mode.color}">${mode.label}</span><strong>${peak ? `${fmtNum(peak.value)} قرارداد` : "—"}</strong>
        <span>${peak ? `اعمال ${fmtNum(peak.strike_price)} ریال${peak.complete && peak.scope_complete !== false ? "" : " (داده ناقص؛ بیشینه در داده‌های موجود)"}` : "داده موجود نیست"}</span></div>`;
    }).join("");
  }

  const priceMarkers = {
    id: "sentimentPriceMarkers",
    afterLayout(chart) {
      const axis = element(chart.canvas.id.replace("Chart", "Axis"));
      if (!axis) return;
      axis.replaceChildren(...chart.scales.y.ticks.map((tick, i) => {
        const label = document.createElement("span");
        label.textContent = compactCount(tick.value);
        label.style.top = `${chart.scales.y.getPixelForTick(i)}px`;
        return label;
      }));
      // Persian compact labels include their unit (e.g. "۱٫۵ میلیون").
      // Measure the actual text so neither the number nor unit gets clipped.
      const axisWidth = Math.max(46, ...[...axis.children].map((label) => Math.ceil(label.getBoundingClientRect().width) + 10));
      axis.parentElement.style.setProperty("--sentiment-axis-width", `${axisWidth}px`);
      const scroller = chart.canvas.parentElement.parentElement;
      const scrollable = scroller.scrollWidth > scroller.clientWidth + 1;
      const suffix = chart.canvas.id.replace("sentiment", "").replace("Chart", "");
      text(`sentiment${suffix}Hint`, scrollable ? "برای دیدن بقیه قیمت‌ها، افقی بکشید؛ برای مقدار دقیق، لمس کنید." : "برای دیدن مقدار دقیق، ستون را لمس کنید.");
      element(`sentiment${suffix}Footer`)?.querySelector("button").classList.toggle("hidden", !scrollable || !finite(model.data?.spot_price));
    },
    afterDatasetsDraw(chart) {
      const data = model.data;
      if (!data) return;
      const { ctx, chartArea: area, scales: { x } } = chart;
      const palette = chartPalette();
      for (const [value, color, level] of [
        [data.spot_price, palette.red, 0],
        [data.atm_strike, palette.purple, 1],
      ]) {
        if (!finite(value)) continue;
        const pixel = x.getPixelForValue(Number(value));
        if (pixel < area.left || pixel > area.right) continue;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.setLineDash(level ? [3, 4] : [7, 3]);
        ctx.beginPath(); ctx.moveTo(pixel, area.top); ctx.lineTo(pixel, area.bottom); ctx.stroke();
        ctx.restore();
      }
    },
  };

  function drawCharts(only = null) {
    if (only === true) only = "Oi";
    const previousCharts = new Set(model.charts.map((chart) => chart.canvas.id));
    const positions = ["Oi", "Change", "Flow"].map((name) => element(`sentiment${name}Chart`)?.parentElement.parentElement.scrollLeft || 0);
    destroyCharts(only);
    if (!model.root || !model.data) return;
    const data = model.data;
    const selectedModes = oiModes.filter((mode) => model.oiModes.includes(mode.key));
    renderOiModes(selectedModes);
    updateFlowStatus();
    model.chartScope ||= {};
    for (const [chartIndex, [suffix, field]] of [["Oi", "oi"], ["Change", "change"], ["Flow", "flow"]].entries()) {
      if (only && suffix !== only) continue;
      const rows = field === "flow" ? model.flowData?.audiences?.[model.flowAudience]?.[model.flowMeasure]?.strikes || [] : data.strikes;
      const cashFlow = field === "flow" && model.flowMeasure === "value";
      const series = field === "oi" ? selectedModes : sides.map((side) => ({ ...side, side: side.key, color: side.key === "put" ? "purple" : "accent" }));
      const scope = `${data.underlying_key}|${data.expiry}|${data.data_date}|${field === "oi" ? model.oiModes.join(",") : field === "flow" ? `${model.flowAudience}|${model.flowMeasure}` : ""}`;
      const sameScope = scope === model.chartScope[suffix];
      model.chartScope[suffix] = scope;
      const canvas = element(`sentiment${suffix}Chart`);
      const wrap = canvas.parentElement;
      const available = rows.some((row) => series.some((mode) => row.coverage[`${mode.side}_${field}`].known > 0 && finite(row[`${mode.side}_${field}`])));
      const allZero = available && rows.every((row) => series.every((mode) => row[`${mode.side}_${field}`] == null || row[`${mode.side}_${field}`] === 0));
      wrap.parentElement.parentElement.classList.toggle("hidden", !available || allZero);
      element(`sentiment${suffix}Footer`).classList.toggle("hidden", !available || allZero);
      text(`sentiment${suffix}Empty`, field === "flow" && (model.flowLoading || model.flowError) ? "" : available ? (allZero ? `${field === "flow" ? "خالص خرید" : field === "oi" ? "موقعیت باز" : "تغییر موقعیت باز"} قراردادهای دارای داده صفر است.` : "")
        : field === "flow" ? "داده کامل خرید و فروش برای قیمت‌های اعمال این انتخاب موجود نیست." : field === "oi" ? (!selectedModes.length ? "برای نمایش نمودار، حداقل یک حالت را انتخاب کنید." : "داده قابل نمایش موقعیت باز برای قیمت‌های اعمال این انتخاب موجود نیست.") : "داده قابل نمایش برای مقایسه با روز معاملاتی قبل موجود نیست.");
      if (!available || allZero || typeof Chart === "undefined") continue;
      wrap.style.width = `max(100%, ${rows.length * Math.max(36, series.length * 8 + 12)}px)`;
      const palette = chartPalette();
      const strikes = rows.map((r) => r.strike_price);
      const spacing = Math.min(...strikes.slice(1).map((v, i) => v - strikes[i])) || strikes[0] * 0.05;
      const padding = Number.isFinite(spacing) ? spacing * 0.6 : Math.max(1, strikes[0] * 0.05);
      const bounds = [...strikes, ...(finite(data.spot_price) ? [data.spot_price] : [])];
      model.charts.push(new Chart(canvas, {
        type: "bar", plugins: [priceMarkers],
        data: { datasets: series.map((mode) => ({
          label: mode.label, backgroundColor: palette[mode.color],
          borderColor: palette[mode.color], borderWidth: 0, maxBarThickness: 18, borderRadius: 3,
          categoryPercentage: 0.85, barPercentage: 0.9,
          data: rows.map((row) => ({ x: row.strike_price, y: cashFlow && finite(row[`${mode.side}_${field}`]) ? row[`${mode.side}_${field}`] / 10 : row[`${mode.side}_${field}`] })),
        })) },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
          layout: { padding: { top: 12, right: 10, bottom: 4, left: 10 } },
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: { rtl: true, textDirection: "rtl", padding: 12, cornerRadius: 10, boxPadding: 5,
              titleFont: { family: "Vazirmatn", size: 12 }, bodyFont: { family: "Vazirmatn", size: 12 }, callbacks: {
              title: (items) => `قیمت اعمال: ${fmtNum(items[0]?.parsed.x)} ریال`,
              label: (item) => {
                if (field === "flow") return `${item.dataset.label}: ${item.parsed.y > 0 ? "+" : ""}${Number(item.parsed.y).toLocaleString("fa-IR", { maximumFractionDigits: cashFlow ? 1 : 0 })} ${cashFlow ? "تومان" : "قرارداد"}`;
                const side = series[item.datasetIndex].side;
                const info = rows[item.dataIndex].coverage[`${side}_${field}`];
                return `${item.dataset.label}: ${field === "change" ? signed(item.parsed.y) : fmtNum(item.parsed.y)} قرارداد${info.complete ? "" : ` · ${coverage(info)}`}`;
              },
            } },
          },
          scales: {
            x: { type: "linear", stacked: false, min: Math.min(...bounds) - padding, max: Math.max(...bounds) + padding,
              afterBuildTicks: (axis) => { axis.ticks = strikes.map((value) => ({ value })); },
              ticks: { color: palette.muted, callback: (v) => fmtNum(v), maxRotation: 0, minRotation: 0, padding: 8, autoSkip: true, autoSkipPadding: 10, font: { family: "Vazirmatn", size: 12 } },
              border: { display: false }, grid: { display: false } },
            y: { beginAtZero: true, stacked: false, afterFit: (axis) => { axis.width = 0; },
              ticks: { display: false, maxTicksLimit: 6, precision: cashFlow ? 1 : 0 }, border: { display: false },
              grid: { drawTicks: false, color: (context) => context.tick?.value === 0 ? palette.muted : palette.grid, lineWidth: (context) => context.tick?.value === 0 ? 1.5 : 1 } },
          },
        },
      }));
      const chart = model.charts[model.charts.length - 1];
      const scroller = wrap.parentElement;
      if (sameScope && previousCharts.has(canvas.id)) scroller.scrollLeft = positions[chartIndex];
      else centerChart(chart);
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
      else if (model.pendingRefresh || ((!data.cached || data.activity?.date_verified === false)
          && !refreshError && !model.attempted.has(target()))) await refresh();
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
