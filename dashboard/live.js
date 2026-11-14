// Client state for the recent-trades table. Ids are history ids, newest first.
//
// The server pages older fills by offset cursor, so any row that lands inside an
// already-consumed range shifts every later offset: a prepend would make the next page
// overlap and skip rows, and an interior insert would never reach the table. The table
// therefore stays as-is only when the store marker and total are unchanged, and the
// newest window is still the exact prefix on screen. Any other change rebuilds from
// the server's newest window, which drops older pages but never skips or repeats rows.
//
// The reducers below are pure. The browser glue at the bottom and the Node regression
// test both call them, so they are the single implementation of this behavior.

function initialTransactionState() {
  return { displayed: [], olderLoaded: false, cursor: null, total: null, marker: null };
}

// snapshot: { ids: string[], cursor: string|null, total: number, marker: string }
function applySnapshot(state, snapshot) {
  if (
    state.olderLoaded &&
    snapshot.total === state.total &&
    snapshot.marker === state.marker &&
    isPrefix(snapshot.ids, state.displayed)
  ) {
    return state;
  }
  return {
    displayed: [...snapshot.ids],
    olderLoaded: false,
    cursor: snapshot.cursor,
    total: snapshot.total,
    marker: snapshot.marker,
  };
}

// page: { ids: string[], requestedCursor: string, nextCursor: string|null, total: number, marker: string }
// A page is only valid if it answers the cursor the state is waiting on and the store
// has not changed since that state was built. Otherwise it is stale and is discarded.
function applyOlderPage(state, page) {
  if (
    page.requestedCursor !== state.cursor ||
    page.total !== state.total ||
    page.marker !== state.marker
  ) {
    return { ...state, stale: true };
  }
  const known = new Set(state.displayed);
  const added = [];
  for (const id of page.ids) {
    if (known.has(id)) continue;
    known.add(id);
    added.push(id);
  }
  return {
    displayed: [...state.displayed, ...added],
    olderLoaded: true,
    cursor: page.nextCursor,
    total: state.total,
    marker: state.marker,
  };
}

function isPrefix(prefix, list) {
  return prefix.length <= list.length && prefix.every((id, index) => list[index] === id);
}

if (typeof module !== "undefined") {
  module.exports = { initialTransactionState, applySnapshot, applyOlderPage };
}

if (typeof window !== "undefined") (() => {
  const setHTML = (id, value) => {
    const element = document.getElementById(id);
    if (element && element.innerHTML !== value) element.innerHTML = value;
  };
  const setText = (id, value) => {
    const element = document.getElementById(id);
    if (element && element.textContent !== value) element.textContent = value;
  };
  let loadingOlder = false;
  let transactionState = initialTransactionState();

  const templateRows = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html;
    return [...template.content.querySelectorAll("tr")];
  };
  const idsOf = (rows) => rows.map((row) => row.dataset.historyId).filter(Boolean);
  const cursorOf = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html;
    const button = template.content.querySelector("button[data-cursor]");
    return button ? button.dataset.cursor : null;
  };

  function bindLoadButton() {
    const button = document.querySelector("#transaction-more button");
    if (!button || button.__loadOlderBound) return;
    button.__loadOlderBound = true;
    button.addEventListener("click", async () => {
      const requestedCursor = transactionState.cursor;
      if (loadingOlder || !requestedCursor) return;
      loadingOlder = true;
      button.disabled = true;
      button.textContent = "Loading…";
      try {
        const response = await fetch(`/transactions?cursor=${encodeURIComponent(requestedCursor)}`, {
          cache: "no-store",
        });
        if (!response.ok) throw new Error("history unavailable");
        const page = await response.json();
        if (page.error) throw new Error(page.error);
        const rows = templateRows(page.rows);
        const next = applyOlderPage(transactionState, {
          ids: idsOf(rows),
          requestedCursor,
          nextCursor: page.next_cursor,
          total: page.total,
          marker: page.transaction_marker,
        });
        if (next.stale) {
          // The table changed while this page was in flight. The next refresh rebuilds it.
          button.textContent = "Load older fills";
          button.disabled = false;
          return;
        }
        transactionState = next;
        const older = document.getElementById("transactions-older");
        const known = new Set(idsOf([...document.querySelectorAll("#transactions-recent tr, #transactions-older tr")]));
        for (const row of rows) {
          if (!known.has(row.dataset.historyId)) older.append(row);
        }
        if (next.cursor) {
          button.dataset.cursor = next.cursor;
          button.textContent = "Load older fills";
          button.disabled = false;
        } else {
          button.parentElement.innerHTML = "";
        }
      } catch {
        button.textContent = "Could not load fills · retry";
        button.disabled = false;
      } finally {
        loadingOlder = false;
      }
    });
  }

  function reconcileRecentTrades(snapshot) {
    const recent = document.getElementById("transactions-recent");
    const older = document.getElementById("transactions-older");
    if (!recent || !older) return;
    transactionState = applySnapshot(transactionState, {
      ids: idsOf(templateRows(snapshot.transactions)),
      cursor: cursorOf(snapshot.transaction_more),
      total: snapshot.transaction_total,
      marker: snapshot.transaction_marker,
    });
    if (transactionState.olderLoaded) return;
    setHTML("transactions-recent", snapshot.transactions);
    older.replaceChildren();
    setHTML("transaction-more", snapshot.transaction_more);
  }

  async function refresh() {
    try {
      const response = await fetch("/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error("snapshot unavailable");
      const snapshot = await response.json();
      setHTML("account-content", snapshot.account);
      setHTML("cash-chart", snapshot.cash_chart);
      setHTML("pnl-chart", snapshot.pnl_chart);
      setHTML("positions-content", snapshot.positions);
      reconcileRecentTrades(snapshot);
      setText("transactions-meta", snapshot.transaction_meta);
      setHTML("activity-content", snapshot.activity);
      setText("activity-meta", snapshot.activity_meta);
      setText("activity-count", snapshot.activity_count);
      setText("live-status", "");
      bindLoadButton();
    } catch {
      setText("live-status", "Live refresh paused · retrying automatically");
    }
    window.setTimeout(refresh, 2000);
  }

  bindLoadButton();
  window.setTimeout(refresh, 2000);
})();
