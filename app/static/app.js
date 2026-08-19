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

function collectToastItems(host) {
  return [
    ...host.querySelectorAll(".toast-item"),
    ...host.querySelectorAll(":scope > .hedron-toast"),
  ].filter((toast) => toast instanceof Element);
}

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
  const items = collectToastItems(host);
  while (items.length > TOAST_MAX) {
    items.shift()?.remove();
  }
}

function hydrateToastHost(host) {
  if (!host || host.dataset.toastHydrated === "true") return;
  host.dataset.toastHydrated = "true";
  const queue = () => {
    pruneToastQueue(host);
    for (const toast of collectToastItems(host)) {
      scheduleToastDismiss(toast);
    }
  };
  queue();
  const observer = new MutationObserver(() => queue());
  observer.observe(host, { childList: true });
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

function staggerReveal(target) {
  if (!target) return;
  const nodes = [...target.children];
  nodes.forEach((node, index) => {
    node.classList.remove("stagger-fade-in");
    node.style.setProperty("--reveal-delay", `${index * 48}ms`);
    void node.offsetWidth;
    node.classList.add("stagger-fade-in");
  });
}

function animateMainPanel(event) {
  const panel = event.detail?.target?.id === "main-panel"
    ? event.detail.target
    : document.getElementById("main-panel");
  if (!panel) return;
  panel.classList.remove("is-entering");
  void panel.offsetWidth;
  panel.classList.add("is-entering");
  staggerReveal(panel);
  window.setTimeout(() => panel.classList.remove("is-entering"), 360);
}

document.addEventListener("DOMContentLoaded", () => {
  revealActiveNavigation();
  const panel = document.getElementById("main-panel");
  if (panel) staggerReveal(panel);
  const host = document.getElementById("toast-host");
  if (host) hydrateToastHost(host);
});

document.addEventListener("htmx:afterSwap", (event) => {
  revealActiveNavigation();
  animateMainPanel(event);
});
