function syncNavigationTabs(root = document) {
  root.querySelectorAll("[data-hedron-navigation-tabs] [role='tab']").forEach((tab) => {
    const selected = tab.getAttribute("aria-selected") === "true";
    const label = tab.dataset.navigationTabLabel || tab.textContent.trim();
    const labelNode = selected ? document.createElement("u") : document.createTextNode(label);

    if (selected) {
      const strong = document.createElement("strong");
      strong.textContent = label;
      labelNode.append(strong);
    }

    tab.dataset.hedronAppearance = "plain";
    tab.dataset.hedronEmphasis = selected ? "primary" : "neutral";
    tab.replaceChildren(labelNode);
  });
}

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
  syncNavigationTabs();
  const nav = document.getElementById("side-nav");
  const active = nav?.querySelector(".hedron-nav-link.active");
  if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return;
  nav.scrollTo({
    left: active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2,
    behavior: "smooth",
  });
});

document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-hedron-navigation-tabs] [role='tab']");
  if (tab) {
    queueMicrotask(() => syncNavigationTabs(tab.closest("[data-hedron-navigation-tabs]")));
  }

  const toggle = event.target.closest("[data-compact-password-toggle]");
  if (!toggle) return;
  queueMicrotask(() => {
    toggle.textContent = toggle.getAttribute("aria-pressed") === "true" ? "Hide" : "Show";
  });
});
