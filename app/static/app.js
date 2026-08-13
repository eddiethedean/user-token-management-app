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
  if (event.detail.elt.id === "pipeline-csv-file") {
    const state = document.getElementById("pipeline-csv-upload-state");
    if (state) state.textContent = "Scanning columns and data types…";
  }
  document.getElementById("global-feedback")?.replaceChildren();
});

document.addEventListener("htmx:afterRequest", (event) => {
  const elt = event.detail.elt;
  elt.removeAttribute("aria-busy");
  if (elt.id === "pipeline-csv-file") {
    const state = document.getElementById("pipeline-csv-upload-state");
    if (state) state.textContent = event.detail.successful ? "Scan complete" : "Scan failed";
  }
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

// Data Mover pipeline demo: durable definitions are server-backed; run telemetry is simulated locally.
const PIPELINE_PROVIDERS = {
  advana: { name: "Advana", mark: "AV", technology: "Databricks", region: "us-gov-west-1" },
  mss: { name: "MSS", mark: "MSS", technology: "Palantir Foundry", region: "us-gov-central-1" },
  postgres: { name: "PostgreSQL", mark: "PG", technology: "PostgreSQL 16", region: "private-vpc" },
  mongodb: { name: "MongoDB", mark: "MDB", technology: "MongoDB 8", region: "document-cluster" },
  csv: { name: "CSV file", mark: "CSV", technology: "Delimited file", region: "Browser upload" },
};
const CREATE_TABLE_VALUE = "__new__";
const NEW_TABLE_VALUE_PREFIX = `${CREATE_TABLE_VALUE}:`;

let activePipelineRun = null;

function pipelineElement(id) {
  return document.getElementById(id);
}

function selectedPipelineOption(select) {
  return select?.options?.[select.selectedIndex] || null;
}

function showPipelineToast(message, tone = "success") {
  const host = pipelineElement("toast-host");
  if (!host) return;
  const item = document.createElement("div");
  item.className = "toast-item";
  item.dataset.toastMs = "3200";
  const toast = document.createElement("div");
  toast.className = `hedron-toast hedron-toast-${tone}`;
  toast.setAttribute("role", "status");
  toast.textContent = message;
  item.append(toast);
  host.append(item);
  pruneToastQueue(host);
  scheduleToastDismiss(item);
}

function pipelineCatalogEntries(provider, schema = "") {
  return [...document.querySelectorAll("#pipeline-catalog-data [data-catalog-table]")]
    .filter((entry) => entry.dataset.catalogProvider === provider)
    .filter((entry) => !schema || entry.dataset.catalogSchema === schema);
}

function pipelineCsvInspection() {
  return pipelineElement("pipeline-csv-inspection");
}

function pipelineCsvReady() {
  return pipelineCsvInspection()?.dataset.csvReady === "true";
}

function pipelineCsvColumns() {
  try {
    return JSON.parse(pipelineCsvInspection()?.dataset.csvColumns || "[]");
  } catch (_error) {
    return [];
  }
}

function syncCsvSourceMode() {
  const source = pipelineElement("pipeline-source-select");
  const picker = document.querySelector(".source-object-picker");
  const panel = pipelineElement("pipeline-csv-upload-panel");
  const usingCsv = source?.value === "csv";
  picker?.classList.toggle("is-csv", usingCsv);
  if (panel) panel.hidden = !usingCsv;
}

function replaceSelectOptions(select, entries, preferred = "", allowCreate = false) {
  if (!select) return;
  const previous = preferred || select.value;
  select.replaceChildren();
  for (const entry of entries) {
    const option = document.createElement("option");
    option.value = entry.value;
    option.textContent = entry.label;
    if (entry.records) option.dataset.records = entry.records;
    if (entry.size) option.dataset.size = entry.size;
    if (entry.megabytes) option.dataset.megabytes = entry.megabytes;
    select.append(option);
  }
  if (allowCreate) {
    const createOption = document.createElement("option");
    createOption.value = CREATE_TABLE_VALUE;
    createOption.textContent = "＋ Create a new table…";
    select.append(createOption);
  }
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

function syncPipelineObjectPicker(kind, preferredSchema = "", preferredTable = "") {
  const provider = pipelineElement(kind === "source" ? "pipeline-source-select" : "pipeline-target-select");
  const schema = pipelineElement(kind === "source" ? "pipeline-source-schema-select" : "pipeline-target-schema-select");
  const table = pipelineElement(kind === "source" ? "pipeline-source-table-select" : "pipeline-target-table-select");
  if (!provider || !schema || !table) return;
  if (!provider.value) {
    replaceSelectOptions(schema, [{ value: "", label: "No connection available" }], "");
    replaceSelectOptions(table, [{ value: "", label: "No connection available" }], "");
    schema.disabled = true;
    table.disabled = true;
    syncCsvSourceMode();
    return;
  }
  if (kind === "source" && provider.value === "csv") {
    const inspection = pipelineCsvInspection();
    const ready = pipelineCsvReady();
    const filename = inspection?.dataset.csvFilename || preferredTable || "Upload required";
    replaceSelectOptions(schema, [{ value: "uploaded", label: "Uploaded file" }], "uploaded");
    replaceSelectOptions(
      table,
      [{
        value: filename,
        label: filename,
        records: inspection?.dataset.csvRows || "0",
        size: inspection?.dataset.csvSize || "—",
        megabytes: inspection?.dataset.csvMegabytes || "0",
      }],
      filename,
    );
    table.disabled = !ready;
    syncCsvSourceMode();
    return;
  }
  schema.disabled = false;
  table.disabled = false;
  const rows = pipelineCatalogEntries(provider.value);
  const schemaNames = [...new Set(rows.map((entry) => entry.dataset.catalogSchema))];
  replaceSelectOptions(
    schema,
    schemaNames.map((name) => ({ value: name, label: name })),
    preferredSchema,
  );
  const selectedSchema = schema.value;
  replaceSelectOptions(
    table,
    rows
      .filter((entry) => entry.dataset.catalogSchema === selectedSchema)
      .map((entry) => ({
        value: entry.dataset.catalogTable,
        label: entry.dataset.catalogTable,
        records: entry.dataset.records,
        size: entry.dataset.size,
        megabytes: entry.dataset.megabytes,
      })),
    preferredTable,
    kind === "target",
  );
  syncCsvSourceMode();
}

function syncPipelineTables(kind, preferredTable = "") {
  const provider = pipelineElement(kind === "source" ? "pipeline-source-select" : "pipeline-target-select");
  const schema = pipelineElement(kind === "source" ? "pipeline-source-schema-select" : "pipeline-target-schema-select");
  const table = pipelineElement(kind === "source" ? "pipeline-source-table-select" : "pipeline-target-table-select");
  if (!provider || !schema || !table) return;
  if (!provider.value) {
    replaceSelectOptions(table, [{ value: "", label: "No connection available" }], "");
    table.disabled = true;
    return;
  }
  table.disabled = false;
  const rows = pipelineCatalogEntries(provider.value, schema.value).map((entry) => ({
    value: entry.dataset.catalogTable,
    label: entry.dataset.catalogTable,
    records: entry.dataset.records,
    size: entry.dataset.size,
    megabytes: entry.dataset.megabytes,
  }));
  replaceSelectOptions(table, rows, preferredTable, kind === "target");
}

function syncNewTableField() {
  const table = pipelineElement("pipeline-target-table-select");
  const field = pipelineElement("pipeline-new-table-field");
  const input = pipelineElement("pipeline-target-table-new");
  const creating = table?.value === CREATE_TABLE_VALUE;
  if (field) field.hidden = !creating;
  if (input) input.required = creating;
}

function isNewTableValue(value) {
  return value === CREATE_TABLE_VALUE || value.startsWith(NEW_TABLE_VALUE_PREFIX);
}

function committedNewTableName(value) {
  return value.startsWith(NEW_TABLE_VALUE_PREFIX)
    ? value.slice(NEW_TABLE_VALUE_PREFIX.length)
    : "";
}

function commitNewTableName() {
  const table = pipelineElement("pipeline-target-table-select");
  const input = pipelineElement("pipeline-target-table-new");
  if (!table || !input || table.value !== CREATE_TABLE_VALUE) return;
  const name = input.value.trim();
  if (!input.checkValidity()) {
    input.reportValidity();
    return;
  }
  const value = `${NEW_TABLE_VALUE_PREFIX}${name}`;
  let option = [...table.options].find((item) => item.value === value);
  if (!option) {
    option = document.createElement("option");
    option.value = value;
    option.textContent = `${name} · new table`;
    const createOption = [...table.options].find((item) => item.value === CREATE_TABLE_VALUE);
    table.insertBefore(option, createOption || null);
  }
  table.value = value;
  input.required = false;
  pipelineElement("pipeline-new-table-field")?.setAttribute("hidden", "");
  updatePipelinePreview("new-table");
  showPipelineToast(`“${name}” is ready to create when you save this pipeline.`);
}

function setProviderPreview(kind, providerKey, providerOption) {
  const specification = PIPELINE_PROVIDERS[providerKey];
  const node = document.querySelector(`.provider-node-${kind}`);
  const heading = pipelineElement(`pipeline-${kind}-name`);
  const connection = pipelineElement(`pipeline-${kind}-connection`);
  if (!node || !heading || !connection) return;
  if (!specification) {
    heading.textContent = "No connection";
    const logo = node.querySelector(".provider-logo");
    if (logo) logo.textContent = "—";
    connection.classList.remove("is-connected", "is-sleeping");
    connection.classList.add("is-demo");
    connection.lastChild.textContent = "Setup required";
    const engine = pipelineElement(`pipeline-${kind}-engine`);
    const region = pipelineElement(`pipeline-${kind}-region`);
    if (engine) engine.textContent = "Configure a connection";
    if (region) region.textContent = "—";
    return;
  }
  heading.textContent = specification.name;
  const logo = node.querySelector(".provider-logo");
  if (logo) logo.textContent = specification.mark;
  const isCsv = kind === "source" && providerKey === "csv";
  const configured = isCsv ? pipelineCsvReady() : providerOption?.dataset.configured === "true";
  const sleeping = configured && providerOption?.dataset.runtime === "sleeping";
  connection.classList.toggle("is-connected", configured);
  connection.classList.toggle("is-sleeping", sleeping);
  connection.classList.toggle("is-demo", !configured);
  connection.lastChild.textContent = isCsv
    ? configured
      ? "File ready"
      : "Upload required"
    : sleeping
      ? "Cluster sleeping"
      : configured
        ? "Stored credentials"
        : "Setup required";
  const engine = pipelineElement(`pipeline-${kind}-engine`);
  const region = pipelineElement(`pipeline-${kind}-region`);
  if (engine) engine.textContent = specification.technology;
  if (region) region.textContent = specification.region;
}

function pipelineRouteState() {
  const source = pipelineElement("pipeline-source-select");
  const target = pipelineElement("pipeline-target-select");
  if (!source || !target || !target.value) {
    return {
      canSave: false,
      canRun: false,
      message: "Set up at least one validated connection before building a pipeline.",
    };
  }

  const sourceOption = selectedPipelineOption(source);
  const targetOption = selectedPipelineOption(target);
  const sourceIsCsv = source.value === "csv";
  const sourceConnected = sourceIsCsv || (
    sourceOption?.dataset.configured === "true" && sourceOption?.dataset.validation === "connected"
  );
  const targetConnected = (
    targetOption?.dataset.configured === "true" && targetOption?.dataset.validation === "connected"
  );
  if (!sourceConnected || !targetConnected) {
    return {
      canSave: false,
      canRun: false,
      message: "Configure and validate both selected connections before using this route.",
    };
  }
  if (!sourceIsCsv && source.value === target.value) {
    return {
      canSave: false,
      canRun: false,
      message: "Choose two different connected systems for this route.",
    };
  }
  if (sourceIsCsv && !pipelineCsvReady()) {
    return {
      canSave: false,
      canRun: false,
      message: "Upload and scan a CSV source before saving or running this route.",
    };
  }

  const sleepingOption = [sourceIsCsv ? null : sourceOption, targetOption]
    .find((option) => option?.dataset.runtime === "sleeping");
  if (sleepingOption) {
    const label = PIPELINE_PROVIDERS[sleepingOption.value]?.name || "selected";
    return {
      canSave: true,
      canRun: false,
      message: `Wake ${label} compute on Connections before running this route.`,
    };
  }
  return {
    canSave: true,
    canRun: true,
    message: "Source and destination connections are ready.",
  };
}

function syncPipelineAvailability() {
  const state = pipelineRouteState();
  const saveButton = document.querySelector("[data-pipeline-save]");
  const runButton = document.querySelector("[data-pipeline-start]");
  const note = pipelineElement("pipeline-availability-note");
  if (saveButton) {
    saveButton.disabled = !state.canSave;
    saveButton.title = state.canSave ? "Save this pipeline for later." : state.message;
  }
  if (runButton && !activePipelineRun) {
    runButton.disabled = !state.canRun;
    runButton.title = state.canRun ? "Run this simulated transfer." : state.message;
  }
  if (note) {
    note.textContent = state.message;
    note.className = `pipeline-availability-note ${state.canRun ? "is-ready" : "is-blocked"}`;
  }
  const status = pipelineElement("pipeline-run-status");
  if (status && !activePipelineRun && !status.classList.contains("is-complete")) {
    status.textContent = state.canRun ? "Ready" : "Blocked";
    status.className = `run-status ${state.canRun ? "is-ready" : "is-blocked"}`;
  }
  return state;
}

function updatePipelinePreview(changedControl = "") {
  const source = pipelineElement("pipeline-source-select");
  const target = pipelineElement("pipeline-target-select");
  const sourceSchema = pipelineElement("pipeline-source-schema-select");
  const sourceTable = pipelineElement("pipeline-source-table-select");
  const targetSchema = pipelineElement("pipeline-target-schema-select");
  const targetTable = pipelineElement("pipeline-target-table-select");
  if (!source || !target || !sourceSchema || !sourceTable || !targetSchema || !targetTable) return;

  if (source.value !== "csv" && source.value === target.value) {
    const replacement = [...target.options].find((option) => option.value !== source.value);
    if (replacement) {
      target.value = replacement.value;
      syncPipelineObjectPicker("target");
    }
    if (changedControl) {
      showPipelineToast("Data Mover selected a different destination to keep the route valid.", "warning");
    }
  }

  const sourceOption = selectedPipelineOption(source);
  const targetOption = selectedPipelineOption(target);
  setProviderPreview("source", source.value, sourceOption);
  setProviderPreview("target", target.value, targetOption);
  const sourceDetail = pipelineElement("pipeline-source-detail");
  const targetDetail = pipelineElement("pipeline-target-detail");
  if (sourceDetail) {
    sourceDetail.textContent = source.value === "csv"
      ? pipelineCsvReady()
        ? pipelineCsvInspection()?.dataset.csvFilename || sourceTable.value
        : "Choose a CSV file"
      : `${sourceSchema.value}.${sourceTable.value}`;
  }
  if (targetDetail) {
    const tableName = isNewTableValue(targetTable.value)
      ? committedNewTableName(targetTable.value) || pipelineElement("pipeline-target-table-new")?.value || "new_table"
      : targetTable.value;
    targetDetail.textContent = target.value
      ? `${targetSchema.value}.${tableName}`
      : "Configure a connection";
  }
  const fieldMap = pipelineElement("pipeline-field-map-label");
  if (fieldMap) {
    const csvColumns = source.value === "csv" ? pipelineCsvColumns() : [];
    const count = source.value === "csv" ? csvColumns.length : 14;
    fieldMap.textContent = count ? `Map ${count} fields` : "Map fields";
    const timeTransform = pipelineElement("pipeline-transform-time");
    const nullTransform = pipelineElement("pipeline-transform-null");
    const keyTransform = pipelineElement("pipeline-transform-key");
    if (source.value === "csv" && csvColumns.length) {
      const timeFields = csvColumns.filter((column) => ["date", "datetime"].includes(column.type)).length;
      const nullableFields = csvColumns.filter((column) => Number(column.nulls || 0) > 0).length;
      if (timeTransform) timeTransform.textContent = timeFields ? `Normalize ${timeFields} date fields` : "Preserve source types";
      if (nullTransform) nullTransform.textContent = nullableFields ? `Profile ${nullableFields} nullable fields` : "No nulls detected";
      if (keyTransform) keyTransform.textContent = `Validate ${csvColumns[0].name}`;
    } else {
      if (timeTransform) timeTransform.textContent = "Normalize timestamps";
      if (nullTransform) nullTransform.textContent = "Drop 3 empty fields";
      if (keyTransform) keyTransform.textContent = "Validate event_id";
    }
  }
  syncCsvSourceMode();
  syncNewTableField();
  syncPipelineAvailability();
}

function resetPipelineRun() {
  if (activePipelineRun) window.clearInterval(activePipelineRun);
  activePipelineRun = null;
  const canvas = pipelineElement("pipeline-canvas");
  canvas?.classList.remove("is-running", "is-complete");
  for (const step of document.querySelectorAll("[data-pipeline-stage]")) {
    step.classList.remove("is-current", "is-complete");
    const state = step.querySelector(".run-step-state");
    if (state) state.textContent = "Waiting";
  }
  for (const bar of document.querySelectorAll("[data-batch-bar]")) {
    bar.classList.remove("is-live");
    bar.style.height = "4px";
  }
}

function setPipelineStage(stageName) {
  const stageOrder = ["auth", "inspect", "transfer", "verify"];
  const currentIndex = stageOrder.indexOf(stageName);
  for (const step of document.querySelectorAll("[data-pipeline-stage]")) {
    const index = stageOrder.indexOf(step.dataset.pipelineStage);
    const state = step.querySelector(".run-step-state");
    step.classList.toggle("is-complete", index < currentIndex);
    step.classList.toggle("is-current", index === currentIndex);
    if (state) {
      state.textContent = index < currentIndex ? "Done" : index === currentIndex ? "Running" : "Waiting";
    }
  }
}

function completePipelineStages() {
  for (const step of document.querySelectorAll("[data-pipeline-stage]")) {
    step.classList.remove("is-current");
    step.classList.add("is-complete");
    const state = step.querySelector(".run-step-state");
    if (state) state.textContent = "Done";
  }
}

function pipelineClock() {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function appendPipelineLog(level, message) {
  const log = pipelineElement("pipeline-run-log");
  if (!log) return;
  const line = document.createElement("p");
  const time = document.createElement("time");
  const label = document.createElement("span");
  time.textContent = pipelineClock();
  label.textContent = level;
  line.append(time, label, document.createTextNode(message));
  log.append(line);
  log.scrollTop = log.scrollHeight;
}

function formatTransferred(megabytes) {
  if (megabytes >= 1024) return `${(megabytes / 1024).toFixed(2)} GB`;
  if (megabytes < 1 / 1024) return `${Math.max(0, Math.round(megabytes * 1024 * 1024))} B`;
  if (megabytes < 1) return `${Math.max(0, Math.round(megabytes * 1024))} KB`;
  return `${Math.max(0, Math.round(megabytes))} MB`;
}

function formatThroughput(megabytesPerSecond) {
  if (megabytesPerSecond < 1 / 1024) {
    return `${Math.max(0, Math.round(megabytesPerSecond * 1024 * 1024))} B/s`;
  }
  if (megabytesPerSecond < 1) return `${(megabytesPerSecond * 1024).toFixed(1)} KB/s`;
  return `${megabytesPerSecond.toFixed(1)} MB/s`;
}

function updateBatchStream(tick, records) {
  const bars = [...document.querySelectorAll("[data-batch-bar]")];
  bars.forEach((bar, index) => {
    const active = index <= tick % bars.length || tick > bars.length;
    bar.classList.toggle("is-live", active);
    bar.style.height = active ? `${10 + ((tick * 11 + index * 7) % 29)}px` : "4px";
  });
  const count = pipelineElement("pipeline-batch-count");
  if (count) count.textContent = `${Math.ceil(records / 5000).toLocaleString()} batches`;
}

function runPipelineTransfer() {
  if (activePipelineRun || !pipelineElement("pipeline-builder")) return;
  updatePipelinePreview();
  const routeState = pipelineRouteState();
  if (!routeState.canRun) {
    showPipelineToast(routeState.message, "warning");
    syncPipelineAvailability();
    return;
  }
  const source = pipelineElement("pipeline-source-select");
  const target = pipelineElement("pipeline-target-select");
  const sourceSchema = pipelineElement("pipeline-source-schema-select");
  const sourceTable = pipelineElement("pipeline-source-table-select");
  const tableOption = selectedPipelineOption(sourceTable);
  if (!source || !target || !sourceSchema || !sourceTable || !tableOption) return;
  const totalRecords = Number(tableOption.dataset.records || "128442");
  const totalMegabytes = Number(tableOption.dataset.megabytes || "1884");
  const sourceName = PIPELINE_PROVIDERS[source.value]?.name || source.value;
  const targetName = PIPELINE_PROVIDERS[target.value]?.name || target.value;
  const fieldCount = source.value === "csv" ? pipelineCsvColumns().length : 14;
  const button = document.querySelector("[data-pipeline-start]");
  const buttonLabel = button?.querySelector(".run-button-label");
  const status = pipelineElement("pipeline-run-status");
  const canvas = pipelineElement("pipeline-canvas");
  const log = pipelineElement("pipeline-run-log");
  const progressTrack = document.querySelector(".pipeline-progress-track");
  const startedAt = performance.now();
  const logged = new Set();
  let progress = 0;
  let tick = 0;

  resetPipelineRun();
  if (log) log.replaceChildren();
  canvas?.classList.add("is-running");
  button?.classList.add("is-running");
  button?.setAttribute("aria-busy", "true");
  if (buttonLabel) buttonLabel.textContent = "Transfer running";
  if (status) {
    status.textContent = "Running";
    status.className = "run-status is-running";
  }
  setPipelineStage("auth");
  appendPipelineLog(
    "START",
    ` Preparing ${sourceName} ${source.value === "csv" ? sourceTable.value : `${sourceSchema.value}.${sourceTable.value}`} → ${targetName} transfer.`,
  );
  appendPipelineLog("AUTH", " Validating source access and destination credentials…");

  const writeMetric = (id, value) => {
    const element = pipelineElement(id);
    if (element) element.textContent = value;
  };

  activePipelineRun = window.setInterval(() => {
    tick += 1;
    if (progress < 12) progress += 2.4;
    else if (progress < 27) progress += 1.9;
    else if (progress < 88) progress += 2.55;
    else progress += 1.55;
    progress = Math.min(100, progress);
    const transferFraction = Math.max(0, Math.min(1, (progress - 24) / 70));
    const records = Math.floor(totalRecords * transferFraction);
    const transferredMB = totalMegabytes * transferFraction;
    const elapsedSeconds = (performance.now() - startedAt) / 1000;
    const throughput = transferFraction > 0 ? transferredMB / Math.max(elapsedSeconds - 2, 0.8) : 0;

    writeMetric("pipeline-progress-value", `${Math.floor(progress)}%`);
    writeMetric("pipeline-records", records ? records.toLocaleString() : "0");
    writeMetric("pipeline-bytes", formatTransferred(transferredMB));
    writeMetric("pipeline-throughput", throughput ? formatThroughput(throughput) : "—");
    writeMetric("pipeline-elapsed", `${elapsedSeconds.toFixed(1)}s`);
    const progressBar = pipelineElement("pipeline-progress-bar");
    if (progressBar) progressBar.style.width = `${progress}%`;
    progressTrack?.setAttribute("aria-valuenow", String(Math.floor(progress)));

    let stage = "auth";
    let progressLabel = "Authenticating connections";
    if (progress >= 12 && progress < 27) {
      stage = "inspect";
      progressLabel = "Inspecting source schema";
      if (!logged.has("schema")) {
        appendPipelineLog(
          "SCHEMA",
          ` ${fieldCount} fields matched · 3 transforms prepared.`,
        );
        logged.add("schema");
      }
    } else if (progress >= 27 && progress < 92) {
      stage = "transfer";
      progressLabel = source.value === "csv"
        ? `Streaming ${sourceTable.value}`
        : `Streaming ${sourceSchema.value}.${sourceTable.value}`;
      updateBatchStream(tick, records);
      if (!logged.has("stream")) {
        appendPipelineLog("STREAM", " Secure stream opened with 5,000-record batches.");
        logged.add("stream");
      }
      if (progress >= 61 && !logged.has("checkpoint")) {
        appendPipelineLog("CHECK", ` ${records.toLocaleString()} records committed at checkpoint.`);
        logged.add("checkpoint");
      }
    } else if (progress >= 92) {
      stage = "verify";
      progressLabel = "Verifying counts and checksum";
      if (!logged.has("verify")) {
        appendPipelineLog("VERIFY", " Reconciling source and destination manifests.");
        logged.add("verify");
      }
    }
    setPipelineStage(stage);
    writeMetric("pipeline-progress-label", progressLabel);

    if (progress >= 100) {
      window.clearInterval(activePipelineRun);
      activePipelineRun = null;
      canvas?.classList.remove("is-running");
      canvas?.classList.add("is-complete");
      button?.classList.remove("is-running");
      button?.removeAttribute("aria-busy");
      if (buttonLabel) buttonLabel.textContent = "Run again";
      if (status) {
        status.textContent = "Completed";
        status.className = "run-status is-complete";
      }
      completePipelineStages();
      writeMetric("pipeline-progress-label", "Transfer complete");
      writeMetric("pipeline-progress-value", "100%");
      writeMetric("pipeline-records", totalRecords.toLocaleString());
      writeMetric("pipeline-bytes", tableOption.dataset.size || formatTransferred(totalMegabytes));
      appendPipelineLog("DONE", ` ${totalRecords.toLocaleString()} records transferred · checksum matched.`);
      showPipelineToast("Transfer completed successfully.");
    }
  }, 280);
}

function renderSavedCsvInspection(button) {
  const root = pipelineCsvInspection();
  if (!root || !button.dataset.pipelineSourceUploadId) return;
  let columns = [];
  try {
    columns = JSON.parse(button.dataset.pipelineSourceUploadColumns || "[]");
  } catch (_error) {
    columns = [];
  }
  root.replaceChildren();
  root.dataset.csvReady = "true";
  root.dataset.csvFilename = button.dataset.pipelineSourceUploadName || "uploaded.csv";
  root.dataset.csvRows = button.dataset.pipelineSourceUploadRows || "0";
  root.dataset.csvColumns = JSON.stringify(columns);
  root.dataset.csvSize = button.dataset.pipelineSourceUploadSize || "—";
  root.dataset.csvMegabytes = button.dataset.pipelineSourceUploadMegabytes || "0";

  const uploadId = document.createElement("input");
  uploadId.type = "hidden";
  uploadId.name = "source_upload_id";
  uploadId.id = "pipeline-source-upload-id";
  uploadId.value = button.dataset.pipelineSourceUploadId;

  const success = document.createElement("div");
  success.className = "csv-inspection-success";
  const heading = document.createElement("div");
  heading.className = "csv-inspection-heading";
  const file = document.createElement("div");
  file.className = "csv-inspection-file";
  const mark = document.createElement("span");
  mark.className = "csv-file-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = "CSV";
  const fileCopy = document.createElement("div");
  const fileName = document.createElement("strong");
  const fileMeta = document.createElement("span");
  fileName.textContent = root.dataset.csvFilename;
  fileMeta.textContent = `${Number(root.dataset.csvRows).toLocaleString()} rows · ${columns.length} columns · ${root.dataset.csvSize}`;
  fileCopy.append(fileName, fileMeta);
  file.append(mark, fileCopy);
  const detected = document.createElement("span");
  detected.className = "connection-health is-connected";
  detected.textContent = "Schema detected";
  heading.append(file, detected);

  const tableWrap = document.createElement("div");
  tableWrap.className = "csv-schema-table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Column", "Inferred type", "Complete", "Example"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const column of columns) {
    const row = document.createElement("tr");
    const total = Number(column.populated || 0) + Number(column.nulls || 0);
    for (const [index, value] of [
      column.name,
      column.type,
      total ? `${Math.round((Number(column.populated || 0) / total) * 100)}%` : "—",
      column.example || "—",
    ].entries()) {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 1) cell.className = `csv-type csv-type-${column.type}`;
      if (index === 3) cell.className = "csv-example";
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  tableWrap.append(table);
  success.append(heading, tableWrap);
  root.append(uploadId, success);
}

function loadSavedPipeline(button) {
  const sourceSelect = pipelineElement("pipeline-source-select");
  const targetSelect = pipelineElement("pipeline-target-select");
  const sourceAvailable = button.dataset.pipelineSource === "csv" || [
    ...(sourceSelect?.options || []),
  ].some((option) => option.value === button.dataset.pipelineSource);
  const targetAvailable = [...(targetSelect?.options || [])]
    .some((option) => option.value === button.dataset.pipelineTarget);
  if (!sourceAvailable || !targetAvailable) {
    showPipelineToast(
      "Reconnect and validate this pipeline’s source and destination before loading it.",
      "warning",
    );
    syncPipelineAvailability();
    return;
  }
  for (const [id, value] of Object.entries({
    "pipeline-id": button.dataset.pipelineId,
    "pipeline-name": button.dataset.pipelineName,
    "pipeline-source-select": button.dataset.pipelineSource,
    "pipeline-target-select": button.dataset.pipelineTarget,
    "pipeline-mode-select": button.dataset.pipelineMode,
    "pipeline-target-table-new": button.dataset.pipelineTargetTableNew,
  })) {
    const element = pipelineElement(id);
    if (element && value !== undefined) element.value = value;
  }
  if (button.dataset.pipelineSource === "csv") renderSavedCsvInspection(button);
  syncPipelineObjectPicker(
    "source",
    button.dataset.pipelineSourceSchema,
    button.dataset.pipelineSourceTable,
  );
  syncPipelineObjectPicker(
    "target",
    button.dataset.pipelineTargetSchema,
    button.dataset.pipelineTargetTable,
  );
  updatePipelinePreview();
  showPipelineToast(`Loaded “${button.dataset.pipelineName}”.`);
  pipelineElement("pipeline-builder")?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (button.dataset.pipelineRun === "true") {
    window.setTimeout(runPipelineTransfer, 450);
  }
}

document.addEventListener("click", (event) => {
  const start = event.target.closest("[data-pipeline-start]");
  if (start) {
    runPipelineTransfer();
    return;
  }
  const load = event.target.closest("[data-pipeline-load]");
  if (load) loadSavedPipeline(load);
});

document.addEventListener("change", (event) => {
  const control = event.target.closest("[data-pipeline-control]");
  if (!control) return;
  const controlName = control.dataset.pipelineControl || "control";
  if (controlName === "source-provider") syncPipelineObjectPicker("source");
  if (controlName === "source-schema") syncPipelineTables("source");
  if (controlName === "target-provider") syncPipelineObjectPicker("target");
  if (controlName === "target-schema") syncPipelineTables("target");
  updatePipelinePreview(controlName);
});

document.addEventListener("input", (event) => {
  if (event.target.closest("#pipeline-target-table-new")) updatePipelinePreview("new-table");
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.target.closest("#pipeline-target-table-new")) return;
  event.preventDefault();
  commitNewTableName();
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest(".pipeline-form");
  if (!form) return;
  updatePipelinePreview();
  const routeState = pipelineRouteState();
  if (!routeState.canSave) {
    event.preventDefault();
    showPipelineToast(routeState.message, "warning");
    syncPipelineAvailability();
    return;
  }
  const source = pipelineElement("pipeline-source-select");
  const target = pipelineElement("pipeline-target-select");
  if (source?.value !== "csv" && source?.value === target?.value) {
    event.preventDefault();
    showPipelineToast("Choose two different systems before saving.", "warning");
    return;
  }
  if (source?.value === "csv" && !pipelineCsvReady()) {
    event.preventDefault();
    showPipelineToast("Upload and scan a CSV before saving this pipeline.", "warning");
    return;
  }
  const saveButton = form.querySelector("[data-pipeline-save]");
  if (saveButton) {
    saveButton.textContent = "Saving…";
    saveButton.setAttribute("aria-busy", "true");
  }
});

function initializePipelineBuilder() {
  if (!pipelineElement("pipeline-builder")) return;
  if (pipelineElement("pipeline-source-select")?.value === "csv") {
    syncPipelineObjectPicker("source");
  } else {
    syncCsvSourceMode();
  }
  updatePipelinePreview();
}

document.addEventListener("DOMContentLoaded", initializePipelineBuilder);
document.addEventListener("htmx:afterSwap", initializePipelineBuilder);
