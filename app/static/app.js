document.addEventListener("htmx:afterRequest", (event) => {
  const elt = event.detail.elt;
  if (event.detail.successful) {
    elt.closest("dialog")?.close();
    const transferStarted =
      elt.matches?.("[data-pipeline-start], [data-pipeline-run]") ||
      elt.closest?.("[data-pipeline-start], [data-pipeline-run]");
    if (transferStarted) {
      document.getElementById("pipeline-workspace-tabs-tab-1")?.click();
    }
  }
});

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
  const toggle = event.target.closest("[data-compact-password-toggle]");
  if (!toggle) return;
  queueMicrotask(() => {
    toggle.textContent = toggle.getAttribute("aria-pressed") === "true" ? "Hide" : "Show";
  });
});
