const colorModeFormSelector = 'form[data-color-mode-form="true"]';

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
  if (themeColor) themeColor.content = normalized === "dark" ? "#191a1b" : "#f4f2eb";

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
    document.getElementById("request-feedback")?.remove();
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

let sideNavRequestPending = false;
document.addEventListener("click", (event) => {
  if (event.target.closest?.("#side-nav a[hx-get]")) sideNavRequestPending = true;
}, true);

document.addEventListener("htmx:afterSettle", (event) => {
  if (!sideNavRequestPending || event.detail.target?.id !== "main-panel") return;
  sideNavRequestPending = false;
  const heading = document.querySelector("#main-panel h1")?.textContent?.trim();
  if (heading) document.title = heading;
  requestAnimationFrame(() => window.scrollTo({ left: 0, top: 0, behavior: "instant" }));
});

document.addEventListener("htmx:responseError", (event) => {
  sideNavRequestPending = false;
  restoreColorMode(event.detail.elt.closest(colorModeFormSelector));
});

document.addEventListener("htmx:sendError", (event) => {
  restoreColorMode(event.detail.elt.closest(colorModeFormSelector));
});

const promotionFormSelector = "form[data-promotion-form]";
const promotionFeedbackId = "promotion-dialog-feedback";
const promotionUnknown = "Refresh the Team directory to check whether the account was promoted.";
const blockedPromotions = new WeakMap();
const directoryRequests = new WeakMap();
let directoryRevision = 0;
let pendingPromotion = null;

function promotionAlert(message) {
  const alert = document.createElement("div");
  alert.className = "hedron-alert hedron-alert-danger";
  alert.setAttribute("role", "alert");
  alert.tabIndex = -1;
  alert.textContent = message;
  return alert;
}

function promotionPageFeedback(message, feedback) {
  if (!feedback) {
    feedback = promotionAlert(message);
    feedback.id = "request-feedback";
    const stale = document.getElementById("request-feedback");
    if (stale) stale.replaceWith(feedback);
    else document.getElementById("hedron-toast")?.append(feedback);
  }
  feedback.tabIndex = -1;
  feedback.focus();
}

function finishPromotionError(request, { summary, close = false, retryable = false, pageFeedback }) {
  pendingPromotion = null;
  const { form, accountId } = request;
  if (!form.isConnected) return;
  const dialog = form.closest("dialog");
  const confirm = form.querySelector("[data-promotion-confirm]");
  confirm.disabled = !retryable;
  if (!retryable) {
    const saved = summary.cloneNode(true);
    saved.removeAttribute("id");
    blockedPromotions.get(form).set(accountId, saved);
  }
  if (dialog.open && !close) {
    dialog.querySelector(`#${promotionFeedbackId}`).replaceChildren(summary);
    summary.focus();
  } else {
    if (dialog.open) dialog.close();
    promotionPageFeedback(summary.textContent, pageFeedback);
  }
}

document.addEventListener("click", (event) => {
  const cancel = event.target.closest?.("[data-promotion-cancel]");
  if (cancel) {
    cancel.closest("dialog").close();
    return;
  }
  const opener = event.target.closest?.("[data-promotion-action]");
  if (!opener) return;
  const dialog = document.getElementById(opener.getAttribute("aria-controls"));
  const form = dialog?.querySelector(promotionFormSelector);
  if (!form || dialog.open || pendingPromotion?.form === form) return;

  const { promotionAction, promotionUserId, promotionUserLabel } = opener.dataset;
  if (new URL(promotionAction, location.href).origin !== location.origin) return;
  // Earlier search, pagination, or action responses belong to the previous
  // directory view. Swapping them would remove this dialog and abort its POST.
  directoryRevision += 1;
  form.setAttribute("action", promotionAction);
  if (window.htmx) {
    form.setAttribute("hx-post", promotionAction);
    window.htmx.process(form);
  }
  form.dataset.promotionUserId = promotionUserId;
  dialog.querySelector("[data-promotion-account-label]").textContent = promotionUserLabel;
  if (!blockedPromotions.has(form)) blockedPromotions.set(form, new Map());
  const savedFeedback = blockedPromotions.get(form).get(promotionUserId);
  const feedback = dialog.querySelector(`#${promotionFeedbackId}`);
  feedback.replaceChildren(...(savedFeedback ? [savedFeedback.cloneNode(true)] : []));
  form.querySelector("[data-promotion-confirm]").disabled = Boolean(savedFeedback);
  opener.focus();
  dialog.showModal();
  form.querySelector("[data-promotion-cancel]").focus();
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches?.(promotionFormSelector)) return;
  const confirm = form.querySelector("[data-promotion-confirm]");
  if (!form.dataset.promotionUserId || confirm.disabled || pendingPromotion?.form === form) {
    event.preventDefault();
    event.stopImmediatePropagation();
  } else if (!window.htmx) {
    confirm.disabled = true;
  }
}, true);

document.addEventListener("htmx:beforeRequest", (event) => {
  const { elt: form, xhr, target } = event.detail;
  if (!form.matches?.(promotionFormSelector)) {
    const directory = target?.closest("#user-directory");
    if (directory) {
      if (
        directory.contains(pendingPromotion?.form ?? null) ||
        directory.querySelector("#administrator-promotion[open]")
      ) {
        event.preventDefault();
      } else {
        directoryRequests.set(xhr, directoryRevision);
      }
    }
    return;
  }
  const confirm = form.querySelector("[data-promotion-confirm]");
  if (confirm.disabled || pendingPromotion?.form === form) {
    event.preventDefault();
    return;
  }
  const request = { form, xhr, accountId: form.dataset.promotionUserId };
  pendingPromotion = request;
  confirm.disabled = true;
  form.closest("dialog").querySelector(`#${promotionFeedbackId}`).replaceChildren();
  const transportFailed = () => {
    if (pendingPromotion === request) {
      finishPromotionError(request, { summary: promotionAlert(promotionUnknown) });
    }
  };
  for (const name of ["error", "timeout", "abort"]) {
    xhr.addEventListener(name, transportFailed, { once: true });
  }
});

document.addEventListener("htmx:beforeSwap", (event) => {
  const { target, xhr, shouldSwap } = event.detail;
  const revision = directoryRequests.get(xhr);
  if (revision !== undefined && revision !== directoryRevision && target?.closest("#user-directory")) {
    event.preventDefault();
    return;
  }
  const request = pendingPromotion;
  if (!request || xhr !== request.xhr) return;
  const directoryResponse = xhr.status === 200 && target?.id === "user-directory-body" &&
    xhr.getResponseHeader("HX-Reswap") === "outerHTML";
  if (shouldSwap && (directoryResponse || [promotionFeedbackId, "hedron-toast"].includes(target?.id))) return;

  // A proxy or an admission error can return a response without the app's fragment headers.
  event.detail.shouldSwap = false;
  const reference = xhr.getResponseHeader("X-Support-Reference");
  const message = reference ? `${promotionUnknown} Reference: ${reference}.` : promotionUnknown;
  finishPromotionError(request, { summary: promotionAlert(message), close: true });
});

document.addEventListener("htmx:afterSettle", (event) => {
  const request = pendingPromotion;
  if (!request || event.detail.xhr !== request.xhr) return;
  if (event.target.id === "user-directory-body" && request.xhr.status === 200) {
    pendingPromotion = null;
    const account = document.getElementById(`directory-account-${request.accountId}`);
    const destination = account ?? document.querySelector("#main-panel h1");
    if (destination) {
      destination.tabIndex = -1;
      destination.focus();
    }
  } else if (event.target.id === promotionFeedbackId) {
    const summary = event.target.querySelector("[data-promotion-error]");
    finishPromotionError(request, {
      summary: summary ?? promotionAlert(promotionUnknown),
      retryable: summary?.dataset.promotionRetryable === "true",
    });
  } else if (event.target.id === "hedron-toast") {
    const pageFeedback = event.target.querySelector(":scope > #request-feedback");
    finishPromotionError(request, {
      summary: pageFeedback ?? promotionAlert(promotionUnknown),
      close: true,
      pageFeedback,
    });
  }
});

function markPipelineDirty(event) {
  const form = event.target.closest?.("#pipeline-form");
  if (!form || !event.target.matches("input, select, textarea")) return;
  if (!form.querySelector('[name="pipeline_id"]')?.value) return;
  form.dataset.pipelineDirty = "true";
  syncPipelineEditor();
}

function syncPipelineEditor() {
  const form = document.getElementById("pipeline-form");
  if (!form) return;
  // Keep the native CSV disclosure accessible when CSV becomes the active source.
  if (form.querySelector("#pipeline-source-select")?.value === "csv") {
    const csvAlternative = document.getElementById("pipeline-csv-alternative");
    if (csvAlternative) csvAlternative.open = true;
  }
  form.querySelectorAll("[data-field-label]").forEach((control) => {
    const label = form.querySelector(`label[for="${control.id}"]`);
    if (label && control.dataset.fieldLabel) label.textContent = control.dataset.fieldLabel;
  });
  const dirty = form.dataset.pipelineDirty === "true";
  const note = document.getElementById("pipeline-unsaved-note");
  if (note) note.hidden = !dirty;
  if (dirty) {
    const run = form.querySelector("[data-pipeline-start]");
    if (run) {
      run.disabled = true;
      run.setAttribute("aria-describedby", "pipeline-unsaved-note");
    }
  }
}

// A run uses the persisted definition. Edits must be saved before it can run.
document.addEventListener("input", markPipelineDirty, true);
document.addEventListener("change", markPipelineDirty, true);
document.addEventListener("change", (event) => {
  if (event.target.matches?.("#pipeline-source-select")) syncPipelineEditor();
});
document.addEventListener("htmx:configRequest", (event) => {
  if (
    event.detail.elt.matches?.("[data-pipeline-start]") &&
    event.detail.elt.closest("#pipeline-form")?.dataset.pipelineDirty === "true"
  ) {
    event.preventDefault();
  }
});

document.addEventListener("htmx:beforeRequest", (event) => {
  const control = event.detail.elt;
  const fileInput = control.matches?.('input[name="csv_file"]')
    ? control
    : control.querySelector?.('input[name="csv_file"]');
  if (!fileInput?.files?.length) return;
  // Clear a previous upload only when a replacement scan actually starts.
  const upload = document.getElementById("pipeline-source-upload-id");
  if (upload) upload.value = "";
});

document.addEventListener("htmx:afterSettle", (event) => {
  const control = event.detail.elt;
  if (
    !control.matches?.('input[name="csv_file"]') &&
    !control.closest?.("#pipeline-csv-file")
  ) return;
  const source = document.getElementById("pipeline-source-select");
  const upload = document.getElementById("pipeline-source-upload-id");
  const inspection = document.getElementById("pipeline-csv-inspection");
  if (inspection?.dataset.csvReady === "true" && source && upload?.value) {
    source.value = "csv";
    source.dispatchEvent(new Event("change", { bubbles: true }));
  }
});

document.addEventListener("click", (event) => {
  const swap = event.target.closest?.("[data-pipeline-swap]");
  if (!swap || swap.disabled || swap.getAttribute("aria-disabled") === "true") return;
  const form = swap.closest("#pipeline-form");
  if (!form?.querySelector('[name="pipeline_id"]')?.value) return;
  form.dataset.pipelineDirty = "true";
  syncPipelineEditor();
});

document.addEventListener("pipelineDatasetCreated", () => {
  document.getElementById("pipeline-target-schema-select")
    ?.dispatchEvent(new Event("change", { bubbles: true }));
});

function syncNewDestinationName() {
  const destinationSelect = document.querySelector("select#pipeline-target-table-select");
  const field = document.querySelector(".data-mover-new-destination-name");
  if (!destinationSelect || !field) return;

  const fileDestination =
    document.getElementById("pipeline-target-select")?.dataset.fileDestination === "true";
  const label = field.querySelector('label[for="pipeline-target-table-new"]');
  if (label) label.textContent = fileDestination ? "New file name" : "New table name";
  const creatingNew = destinationSelect.value === "__new__";
  field.hidden = !creatingNew;
  const input = field.querySelector('input[name="destination_table_new"]');
  if (input) {
    input.disabled = !creatingNew;
    input.required = creatingNew;
  }
}

document.addEventListener("invalid", (event) => {
  const panel = event.target.closest?.('#pipeline-setup-tabs [role="tabpanel"]');
  if (!panel) return;
  const tab = document.querySelector(
    `#pipeline-setup-tabs [role="tab"][aria-controls="${panel.id}"]`,
  );
  if (tab?.getAttribute("aria-selected") !== "true") tab?.click();
}, true);

document.addEventListener("htmx:afterSettle", () => {
  syncNewDestinationName();
  syncPipelineEditor();
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest?.('form[data-diagnostic-run-form="true"]');
  const button = form?.querySelector("[data-diagnostic-run-submit]");
  if (!button || button.disabled) return;
  button.disabled = true;
  button.textContent = "Starting connection checks…";
});

let announcedDiagnosticMessage = null;
function syncDiagnosticAnnouncement(overview, { announceInitial = false } = {}) {
  if (!overview) return;
  const message = overview.dataset.announcement || "";
  const announcement = document.getElementById("diagnostic-announcement");
  if (!announcement || !message) return;
  if (announcedDiagnosticMessage === message) return;
  announcedDiagnosticMessage = message;
  if (announceInitial || overview.dataset.status !== "queued") {
    if (announcement.textContent !== message) announcement.textContent = message;
  }
}

function initializeDiagnosticAnnouncements() {
  syncDiagnosticAnnouncement(document.getElementById("diagnostic-run"), {
    announceInitial: true,
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeDiagnosticAnnouncements, { once: true });
} else {
  initializeDiagnosticAnnouncements();
}

document.addEventListener("htmx:afterSettle", () => {
  syncDiagnosticAnnouncement(document.getElementById("diagnostic-run"));
});

let diagnosticControlHadFocus = false;
document.addEventListener("htmx:beforeRequest", (event) => {
  if (event.detail.elt?.id !== "diagnostic-run-control") return;
  diagnosticControlHadFocus = event.detail.elt.contains(document.activeElement);
});

document.addEventListener("htmx:afterRequest", (event) => {
  if (event.detail.elt?.id === "diagnostic-run-control" && event.detail.xhr.status === 204) {
    diagnosticControlHadFocus = false;
  }
});

document.addEventListener("htmx:afterSettle", (event) => {
  if (
    event.detail.elt?.id !== "diagnostic-run-control" ||
    !diagnosticControlHadFocus
  ) return;
  diagnosticControlHadFocus = false;
  const control = document.getElementById("diagnostic-run-control");
  requestAnimationFrame(() => {
    const enabledButton = control?.querySelector(
      "[data-diagnostic-run-submit]:not(:disabled)",
    );
    if (enabledButton) enabledButton.focus();
    else control?.querySelector("a")?.focus();
  });
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest?.('[data-diagnostic-copy="true"]');
  if (!button) return;
  const report = button.dataset.report || "";
  const announcement = document.getElementById("diagnostic-announcement");
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard access unavailable");
    await navigator.clipboard.writeText(report);
    button.textContent = "Report copied";
    if (announcement) announcement.textContent = "Readiness report copied to the clipboard.";
  } catch {
    const container = document.getElementById("diagnostic-report-fallback-container");
    const textarea = document.getElementById("diagnostic-report-fallback");
    if (!container || !textarea) return;
    textarea.value = report;
    container.hidden = false;
    textarea.focus();
    textarea.select();
    if (announcement) {
      announcement.textContent = "Clipboard access is unavailable. The report text is selected below for manual copying.";
    }
  }
});

document.addEventListener("htmx:oobAfterSwap", () => {
  syncNewDestinationName();
  syncPipelineEditor();
});

document.addEventListener("DOMContentLoaded", () => {
  const nav = document.getElementById("side-nav");
  const active = nav?.querySelector('[data-hedron-nav-link="true"].active');
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
  if (event.target.closest("#pipeline-target-table-select")) {
    syncNewDestinationName();
    if (event.target.value === "__new__") {
      document.getElementById("pipeline-setup-tabs-tab-1")?.click();
    }
  }

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

syncNewDestinationName();
syncPipelineEditor();
