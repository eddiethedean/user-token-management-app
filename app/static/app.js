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
document.addEventListener("htmx:configRequest", (event) => {
  if (
    event.detail.elt.matches?.("[data-pipeline-start]") &&
    event.detail.elt.closest("#pipeline-form")?.dataset.pipelineDirty === "true"
  ) {
    event.preventDefault();
  }
});

document.addEventListener("change", (event) => {
  if (!event.target.matches('#pipeline-csv-file input[type="file"]')) return;
  // Never keep a previous upload active while its replacement is being scanned.
  const upload = document.getElementById("pipeline-source-upload-id");
  if (upload) upload.value = "";
}, true);

document.addEventListener("htmx:afterRequest", (event) => {
  if (!event.detail.elt.closest?.("#pipeline-csv-file")) return;
  const source = document.getElementById("pipeline-source-select");
  const upload = document.getElementById("pipeline-source-upload-id");
  if (event.detail.successful && source && upload?.value) {
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
  }
}

document.addEventListener("htmx:afterSettle", () => {
  syncNewDestinationName();
  syncPipelineEditor();
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
