document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-password-toggle]");
  if (!button) return;
  const input = document.getElementById(button.dataset.passwordToggle);
  if (!input) return;
  const revealing = input.type === "password";
  input.type = revealing ? "text" : "password";
  button.setAttribute("aria-pressed", revealing ? "true" : "false");
  button.textContent = revealing ? "Hide password" : "Show password";
});

document.addEventListener("htmx:beforeRequest", (event) => {
  event.detail.elt.setAttribute("aria-busy", "true");
  document.getElementById("global-feedback")?.replaceChildren();
});

document.addEventListener("htmx:afterRequest", (event) => {
  const elt = event.detail.elt;
  elt.removeAttribute("aria-busy");
  if (event.detail.successful) {
    elt.closest("dialog")?.close();
  }
});

const TOAST_MAX = 4;
const TOAST_DEFAULT_MS = 4500;

function scheduleToastDismiss(toastItem) {
  if (toastItem.dataset.toastScheduled) return;
  toastItem.dataset.toastScheduled = "1";
  const ms = Number(toastItem.dataset.toastMs) || TOAST_DEFAULT_MS;
  window.setTimeout(() => {
    toastItem.classList.add("is-leaving");
    window.setTimeout(() => toastItem.remove(), 220);
  }, ms);
}

function pruneToastQueue(host) {
  const items = [...host.querySelectorAll(".toast-item")];
  while (items.length > TOAST_MAX) {
    items.shift()?.remove();
  }
}

function revealActiveNavigation() {
  const nav = document.getElementById("side-nav");
  const active = nav?.querySelector(".nav-link.active");
  if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return;
  nav.scrollTo({
    left: active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2,
    behavior: "smooth",
  });
}

function animateMainPanel(event) {
  if (event.detail?.target?.id !== "main-panel") return;
  const panel = document.getElementById("main-panel");
  if (!panel) return;
  panel.classList.remove("is-entering");
  void panel.offsetWidth;
  panel.classList.add("is-entering");
  window.setTimeout(() => panel.classList.remove("is-entering"), 300);
}

document.addEventListener("DOMContentLoaded", revealActiveNavigation);

document.addEventListener("htmx:afterSwap", (event) => {
  revealActiveNavigation();
  animateMainPanel(event);
  const host = document.getElementById("toast-host");
  if (!host) return;
  pruneToastQueue(host);
  for (const item of host.querySelectorAll(".toast-item")) {
    scheduleToastDismiss(item);
  }
  // Legacy bare toasts (no wrapper) still auto-dismiss.
  for (const toast of host.querySelectorAll(":scope > .hedron-toast")) {
    if (toast.closest(".toast-item")) continue;
    window.setTimeout(() => toast.remove(), TOAST_DEFAULT_MS);
  }
});

let historyRestoreInFlight = false;
document.addEventListener("htmx:historyRestore", (event) => {
  // Cache-miss restores already fetched a fresh document from the server.
  if (event.detail?.cacheMiss) return;
  if (!window.htmx || historyRestoreInFlight) return;
  const panel = document.getElementById("main-panel");
  if (!panel) return;
  const path =
    event.detail?.path ||
    `${window.location.pathname}${window.location.search}`;
  historyRestoreInFlight = true;
  window.htmx
    .ajax("GET", path, {
      target: "#main-panel",
      swap: "outerHTML",
      select: "#main-panel",
      selectOOB: "#side-nav",
      headers: { "HX-History-Restore-Request": "true" },
    })
    .finally(() => {
      historyRestoreInFlight = false;
    });
});

function lazyLoadFailed(event) {
  const elt = event.detail.elt;
  if (!elt || elt.getAttribute("hx-trigger") !== "load") return;
  const targetId = elt.id;
  if (!targetId) return;
  const path =
    elt.getAttribute("hx-get") ||
    elt.getAttribute("hx-post") ||
    window.location.pathname;
  elt.outerHTML = [
    `<div id="${targetId}" class="event-list" data-lazy-error="${targetId}">`,
    `<div class="hedron-error" role="group">`,
    `<p role="alert">Could not load this section.</p>`,
    `<button type="button" hx-get="${path}" hx-swap="outerHTML" hx-target="#${targetId}">Retry</button>`,
    `</div></div>`,
  ].join("");
  if (window.htmx) window.htmx.process(document.getElementById(targetId));
}

document.addEventListener("htmx:responseError", lazyLoadFailed);
document.addEventListener("htmx:sendError", lazyLoadFailed);
