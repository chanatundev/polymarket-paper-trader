(function () {
  "use strict";

  const state = {
    query: {},
    cursors: [null],
    page: null,
    selectedId: null,
  };

  const elements = {
    form: document.getElementById("filters"),
    rows: document.getElementById("trade-rows"),
    historyStatus: document.getElementById("history-status"),
    historyEmpty: document.getElementById("history-empty"),
    resultMeta: document.getElementById("result-meta"),
    pageMeta: document.getElementById("page-meta"),
    previous: document.getElementById("previous-page"),
    next: document.getElementById("next-page"),
    refresh: document.getElementById("refresh-button"),
    clear: document.getElementById("clear-filters"),
    detailEmpty: document.getElementById("detail-empty"),
    detailContent: document.getElementById("detail-content"),
    detailTitle: document.getElementById("detail-title"),
    detailSource: document.getElementById("detail-source"),
    detailId: document.getElementById("detail-id"),
    detailWarning: document.getElementById("detail-warning"),
    detailSections: document.getElementById("detail-sections"),
    storePath: document.getElementById("store-path"),
    connectionStatus: document.getElementById("connection-status"),
  };

  const FILTER_FIELDS = ["search", "side", "status", "source_kind", "market", "outcome", "run_id"];
  const DETAIL_SECTIONS = [
    ["identity", "Identity"],
    ["order", "Order"],
    ["fill", "Fill"],
    ["market", "Market"],
    ["execution", "Execution"],
    ["accounting", "Accounting"],
    ["timestamps", "Timestamps"],
    ["evidence", "Evidence"],
    ["assumptions", "Assumptions"],
  ];

  function setHistoryStatus(message, kind) {
    elements.historyStatus.textContent = message;
    elements.historyStatus.className = "status-line" + (kind ? " " + kind : "");
  }

  function apiError(payload, fallback) {
    const errors = payload && Array.isArray(payload.errors) ? payload.errors : [];
    const message = errors.length && errors[0].message ? errors[0].message : fallback;
    const error = new Error(message);
    error.payload = payload;
    return error;
  }

  async function getJSON(url) {
    let response;
    try {
      response = await fetch(url, { headers: { Accept: "application/json" } });
    } catch (_error) {
      throw new Error("The local GUI server could not be reached.");
    }
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error("The local server returned an unreadable response.");
    }
    if (!response.ok || !payload.ok) {
      throw apiError(payload, "The local history request failed.");
    }
    return payload;
  }

  function queryString(query, cursor) {
    const params = new URLSearchParams();
    Object.keys(query).forEach(function (key) {
      if (query[key]) params.set(key, query[key]);
    });
    params.set("limit", "25");
    if (cursor) params.set("cursor", cursor);
    return params.toString();
  }

  async function loadHistory(cursor, pageIndex) {
    setHistoryStatus("Loading local history…", "");
    elements.historyEmpty.hidden = true;
    elements.rows.replaceChildren();
    try {
      const payload = await getJSON("/api/trades?" + queryString(state.query, cursor));
      state.page = payload.result;
      renderPage(payload.result, pageIndex);
      renderServerState(payload);
      if (state.page.records.length) {
        setHistoryStatus("Select a record to inspect its evidence.", "");
      } else {
        setHistoryStatus("No records matched the current view.", "notice");
        elements.historyEmpty.hidden = false;
      }
    } catch (error) {
      state.page = null;
      elements.resultMeta.textContent = "Unavailable";
      elements.pageMeta.textContent = "No page";
      elements.previous.disabled = true;
      elements.next.disabled = true;
      setHistoryStatus(error.message, "error");
      elements.historyEmpty.hidden = false;
      elements.historyEmpty.querySelector(".empty-kicker").textContent = "Local store unavailable";
      elements.historyEmpty.querySelector("p:last-child").textContent = "Check the data-directory path and restart the local GUI. No database was created by this view.";
    }
  }

  function renderServerState(payload) {
    const assumptions = payload.assumptions || {};
    elements.connectionStatus.textContent = "LOCAL STORE · READ-ONLY";
    elements.storePath.textContent = assumptions.database_path ? "Store: " + assumptions.database_path : "Store path reported by local server.";
  }

  function renderPage(page, pageIndex) {
    const records = Array.isArray(page.records) ? page.records : [];
    elements.resultMeta.textContent = page.total_is_complete ? page.total + " record" + (page.total === 1 ? "" : "s") : "Up to " + page.total + " scanned";
    elements.pageMeta.textContent = "Page " + (pageIndex + 1);
    elements.previous.disabled = pageIndex < 1;
    elements.next.disabled = !page.next_cursor;
    records.forEach(function (record) {
      elements.rows.appendChild(makeRow(record));
    });
  }

  function makeRow(record) {
    const row = document.createElement("tr");
    const market = record.market || {};
    const execution = record.execution || {};
    const accounting = record.accounting || {};
    const marketCell = document.createElement("td");
    const select = document.createElement("button");
    select.type = "button";
    select.className = "trade-select";
    select.textContent = market.question || market.slug || market.asset_id || market.market_id || "Unknown market";
    select.title = "Open trade " + String(record.history_id || "");
    select.addEventListener("click", function () { loadDetail(record.history_id, select); });
    marketCell.appendChild(select);
    const subline = document.createElement("span");
    subline.className = "trade-subline";
    subline.textContent = (market.outcome || "Unknown outcome") + " · " + (record.source_kind || "unknown source");
    marketCell.appendChild(subline);
    row.appendChild(makeCell(formatTime(record.occurred_at)));
    row.appendChild(marketCell);
    const side = makeCell(titleCase(execution.side));
    side.className = "side-" + String(execution.side || "").toLowerCase();
    row.appendChild(side);
    row.appendChild(makeStatusCell(execution.status));
    row.appendChild(makeCell(display(execution.filled_quantity), true));
    row.appendChild(makeCell(display(execution.price), true));
    row.appendChild(makeCell(display(execution.notional), true));
    row.appendChild(makeCell(display(accounting.fee), true));
    return row;
  }

  function makeCell(value, numeric) {
    const cell = document.createElement("td");
    if (numeric) cell.className = "numeric";
    cell.textContent = value;
    return cell;
  }

  function makeStatusCell(status) {
    const cell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "status-pill " + String(status || "").toLowerCase();
    badge.textContent = titleCase(status || "Unknown");
    cell.appendChild(badge);
    return cell;
  }

  async function loadDetail(historyId, focusTarget) {
    if (!historyId) return;
    elements.detailEmpty.hidden = true;
    elements.detailContent.hidden = false;
    elements.detailTitle.textContent = "Loading detail…";
    elements.detailSource.textContent = "";
    elements.detailId.textContent = "";
    elements.detailWarning.hidden = true;
    elements.detailSections.replaceChildren();
    try {
      const payload = await getJSON("/api/trades/" + encodeURIComponent(historyId));
      const record = payload.result;
      state.selectedId = historyId;
      renderDetail(record);
      if (focusTarget) focusTarget.focus();
    } catch (error) {
      elements.detailTitle.textContent = "Detail unavailable";
      elements.detailWarning.hidden = false;
      elements.detailWarning.textContent = error.message;
    }
  }

  function renderDetail(record) {
    const market = record.market || {};
    elements.detailTitle.textContent = market.question || market.slug || market.asset_id || "Trade detail";
    elements.detailSource.textContent = titleCase(record.source_kind || "unknown source");
    elements.detailId.textContent = String(record.history_id || "Unknown record ID");
    const warnings = Array.isArray(record.warnings) ? record.warnings : [];
    const unknownReasons = record.unknown_reasons || {};
    if (warnings.length) {
      elements.detailWarning.hidden = false;
      elements.detailWarning.textContent = "Warnings: " + warnings.join(" · ");
    }
    DETAIL_SECTIONS.forEach(function (section) {
      const values = record[section[0]];
      if (values && typeof values === "object" && Object.keys(values).length) {
        elements.detailSections.appendChild(makeDetailSection(section[1], values, unknownReasons, section[0]));
      }
    });
    if (Object.keys(unknownReasons).length) {
      elements.detailSections.appendChild(makeDetailSection("Unknowns kept visible", unknownReasons, {}, ""));
    }
    if (record.related && Array.isArray(record.related.order_events) && record.related.order_events.length) {
      const latestEvent = record.related.order_events[record.related.order_events.length - 1] || {};
      elements.detailSections.appendChild(makeDetailSection("Order events", { count: record.related.order_events.length, latest_status: latestEvent.status }, {}, ""));
    }
  }

  function makeDetailSection(title, values, unknownReasons, prefix) {
    const section = document.createElement("section");
    section.className = "detail-section";
    const heading = document.createElement("h3");
    heading.textContent = title;
    section.appendChild(heading);
    const list = document.createElement("dl");
    Object.keys(values).forEach(function (key) {
      const term = document.createElement("dt");
      term.textContent = labelize(key);
      const description = document.createElement("dd");
      const path = prefix ? prefix + "." + key : key;
      const value = values[key];
      const reason = unknownReasons[path];
      if (value === null || value === undefined || value === "") {
        description.className = "unknown";
        description.textContent = reason ? "Unknown — " + reason : "Unknown";
      } else if (Array.isArray(value)) {
        description.textContent = value.map(function (item) { return typeof item === "object" ? JSON.stringify(item) : String(item); }).join(" · ");
      } else if (typeof value === "object") {
        description.textContent = JSON.stringify(value);
      } else {
        description.textContent = String(value);
      }
      list.appendChild(term);
      list.appendChild(description);
    });
    section.appendChild(list);
    return section;
  }

  function readFilters() {
    const query = {};
    FILTER_FIELDS.forEach(function (field) {
      const element = document.getElementById(field.replace(/_/g, "-"));
      if (element && element.value.trim()) query[field] = element.value.trim();
    });
    return query;
  }

  function clearFilters() {
    FILTER_FIELDS.forEach(function (field) {
      const element = document.getElementById(field.replace(/_/g, "-"));
      if (element) element.value = "";
    });
    state.query = {};
    state.cursors = [null];
    loadHistory(null, 0);
  }

  function titleCase(value) {
    return String(value || "unknown").replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
  }

  function labelize(value) {
    return titleCase(value).replace(/Id\b/g, "ID").replace(/Pnl\b/g, "P&L");
  }

  function formatTime(value) {
    if (!value) return "Unknown";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function display(value) {
    return value === null || value === undefined || value === "" ? "—" : String(value);
  }

  elements.form.addEventListener("submit", function (event) {
    event.preventDefault();
    state.query = readFilters();
    state.cursors = [null];
    loadHistory(null, 0);
  });
  elements.clear.addEventListener("click", clearFilters);
  elements.refresh.addEventListener("click", function () { loadHistory(state.cursors[state.cursors.length - 1], state.cursors.length - 1); });
  elements.previous.addEventListener("click", function () {
    if (state.cursors.length < 2) return;
    state.cursors.pop();
    loadHistory(state.cursors[state.cursors.length - 1], state.cursors.length - 1);
  });
  elements.next.addEventListener("click", function () {
    if (!state.page || !state.page.next_cursor) return;
    state.cursors.push(state.page.next_cursor);
    loadHistory(state.page.next_cursor, state.cursors.length - 1);
  });

  loadHistory(null, 0);
}());
