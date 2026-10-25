/** Shared local API and activation flow for dashboard pages. */
async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await res.text();
  if (!res.ok) {
    if (res.status === 423) setActivationGate(true);
    let message = res.statusText || "خطا در ارتباط با سرور";
    try {
      const parsed = text ? JSON.parse(text) : null;
      message = parsed?.detail || parsed?.message || message;
    } catch {
      // Do not display HTML returned by a proxy as application markup.
    }
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("پاسخ نامعتبر از سرور");
  }
}

function setActivationGate(locked, message = "") {
  document.getElementById("activationOverlay")?.classList.toggle("hidden", !locked);
  const error = document.getElementById("activationError");
  if (error) {
    error.textContent = message;
    error.classList.toggle("hidden", !message);
  }
  if (locked) requestAnimationFrame(() => document.getElementById("activationCode")?.focus());
}

async function ensureActivation() {
  try {
    const status = await api("/api/activation/status");
    const activated = status.activated === true;
    setActivationGate(!activated);
    return activated;
  } catch (error) {
    setActivationGate(true, "بررسی وضعیت فعال‌سازی انجام نشد؛ دوباره تلاش کنید.");
    throw error;
  }
}

function bindActivation(onActivated) {
  document.getElementById("activationForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("activationCode");
    const submit = document.getElementById("activationSubmit");
    const code = input?.value.trim() || "";
    if (!code) return setActivationGate(true, "کد را وارد کنید");
    submit.disabled = true;
    try {
      const result = await api("/api/activation", { method: "POST", body: JSON.stringify({ code }) });
      if (!result.activated) throw new Error("کد پذیرفته نشد");
      input.value = "";
      setActivationGate(false);
      await onActivated();
    } catch (error) {
      setActivationGate(true, error.message || "کد پذیرفته نشد");
    } finally {
      submit.disabled = false;
    }
  });
}
