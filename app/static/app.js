const colorModeFormSelector = 'form[data-color-mode-form="true"]';
const navCollapseStorageKey = "data-mover-nav-collapsed";

function storedNavCollapsePreference() {
  try {
    return window.localStorage.getItem(navCollapseStorageKey) === "true";
  } catch {
    return false;
  }
}

function setNavCollapsed(collapsed, { persist = true } = {}) {
  const shell = document.querySelector(".hedron-app-shell");
  const toggle = document.getElementById("side-nav-toggle");
  if (!shell || !toggle) return;

  shell.dataset.navCollapsed = String(collapsed);
  toggle.setAttribute("aria-controls", "side-nav");
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute(
    "aria-label",
    collapsed ? "Expand navigation" : "Collapse navigation",
  );
  toggle.title = collapsed ? "Expand navigation" : "Collapse navigation";
  const icon = toggle.querySelector("span");
  if (icon) icon.textContent = collapsed ? "›" : "‹";

  if (persist) {
    try {
      window.localStorage.setItem(navCollapseStorageKey, String(collapsed));
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }
}

function initializeNavCollapse() {
  setNavCollapsed(storedNavCollapsePreference(), { persist: false });
}

function applyColorMode(mode) {
  const normalized = mode === "dark" ? "dark" : "light";
  document.querySelectorAll("[data-theme]").forEach((element) => {
    element.dataset.theme = normalized;
  });
  document.querySelectorAll("[data-hedron-color-mode]").forEach((element) => {
    element.dataset.hedronColorMode = normalized;
  });

  const colorScheme = document.querySelector('meta[name="color-scheme"]');
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (colorScheme) colorScheme.content = normalized;
  if (themeColor) themeColor.content = normalized === "dark" ? "#080d1a" : "#f4f6fb";

  document.querySelectorAll('img[src*="/data-mover-mark-"]').forEach((image) => {
    image.src = image.src.replace(
      /data-mover-mark-(?:light|dark)\.png/,
      `data-mover-mark-${normalized}.png`,
    );
  });
}

function rememberColorModeScroll(form) {
  form.dataset.colorModeScrollX = String(window.scrollX);
  form.dataset.colorModeScrollY = String(window.scrollY);
}

function restoreColorModeScroll(form) {
  if (!form?.dataset.colorModeScrollY) return;
  window.scrollTo({
    left: Number(form.dataset.colorModeScrollX || 0),
    top: Number(form.dataset.colorModeScrollY),
    behavior: "instant",
  });
}

function clearColorModeTransition(form) {
  if (!form) return;
  delete form.dataset.previousColorMode;
  delete form.dataset.colorModeScrollX;
  delete form.dataset.colorModeScrollY;
}

function restoreColorMode(form) {
  const previous = form?.dataset.previousColorMode;
  if (!previous) return;
  applyColorMode(previous);
  const toggle = form.querySelector('input[name="dark_mode"]');
  if (toggle) {
    toggle.checked = previous === "dark";
    toggle.setAttribute("aria-checked", String(toggle.checked));
  }
  restoreColorModeScroll(form);
  clearColorModeTransition(form);
}

document.addEventListener("htmx:afterRequest", (event) => {
  const elt = event.detail.elt;
  if (event.detail.successful) {
    const colorModeForm = elt.closest(colorModeFormSelector);
    restoreColorModeScroll(colorModeForm);
    clearColorModeTransition(colorModeForm);
    elt.closest("dialog")?.close();
    const transferStarted =
      elt.matches?.("[data-pipeline-start], [data-pipeline-run]") ||
      elt.closest?.("[data-pipeline-start], [data-pipeline-run]");
    if (transferStarted) {
      document.getElementById("pipeline-workspace-tabs-tab-1")?.click();
    }
  }
});

document.addEventListener("htmx:responseError", (event) => {
  restoreColorMode(event.detail.elt.closest(colorModeFormSelector));
});

document.addEventListener("htmx:sendError", (event) => {
  restoreColorMode(event.detail.elt.closest(colorModeFormSelector));
});

document.addEventListener("htmx:afterSettle", initializeNavCollapse);

document.addEventListener("DOMContentLoaded", () => {
  const nav = document.getElementById("side-nav");
  const active = nav?.querySelector(".hedron-nav-link.active");
  if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return;
  nav.scrollTo({
    left: active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2,
    behavior: "smooth",
  });
});

document.addEventListener("click", (event) => {
  const navToggle = event.target.closest("#side-nav-toggle");
  if (navToggle) {
    const shell = navToggle.closest(".hedron-app-shell");
    setNavCollapsed(shell?.dataset.navCollapsed !== "true");
    return;
  }

  const toggle = event.target.closest("[data-compact-password-toggle]");
  if (!toggle) return;
  queueMicrotask(() => {
    toggle.textContent = toggle.getAttribute("aria-pressed") === "true" ? "Hide" : "Show";
  });
});

document.addEventListener(
  "pointerdown",
  (event) => {
    const modeToggle = event.target.closest(
      '[data-hedron-mark="color-mode-toggle"] input[type="checkbox"]',
    );
    if (modeToggle?.form) rememberColorModeScroll(modeToggle.form);
  },
  { passive: true },
);

document.addEventListener("change", (event) => {
  const modeToggle = event.target.closest(
    '[data-hedron-mark="color-mode-toggle"] input[type="checkbox"]',
  );
  if (!modeToggle?.form) return;
  if (!modeToggle.form.dataset.colorModeScrollY) {
    rememberColorModeScroll(modeToggle.form);
  }
  modeToggle.form.dataset.previousColorMode =
    document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  applyColorMode(modeToggle.checked ? "dark" : "light");
  restoreColorModeScroll(modeToggle.form);
  modeToggle.form.requestSubmit();
});

initializeNavCollapse();
