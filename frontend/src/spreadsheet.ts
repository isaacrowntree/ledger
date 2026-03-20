import { api, type SpreadsheetOutgoing, type SpreadsheetIncoming, type RentalProperty, type WorkTripsResponse } from "./api";

let currentSubTab = "outgoing";

export function initSpreadsheet() {
  document.querySelectorAll<HTMLButtonElement>(".ss-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".ss-tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".ss-view").forEach((v) => v.classList.remove("active"));
      btn.classList.add("active");
      currentSubTab = btn.dataset.sstab!;
      document.getElementById(`ss-${currentSubTab}`)!.classList.add("active");
      loadSpreadsheetSubTab(currentSubTab);
    });
  });
}

export async function loadSpreadsheet() {
  await loadSpreadsheetSubTab(currentSubTab);
}

function getSpreadsheetFY(): string | undefined {
  return (document.getElementById("ss-fy") as HTMLSelectElement)?.value;
}

async function loadSpreadsheetSubTab(tab: string) {
  const fy = getSpreadsheetFY();
  switch (tab) {
    case "outgoing":
      return renderOutgoing(await api.spreadsheetOutgoing(fy));
    case "incoming":
      return renderIncoming(await api.spreadsheetIncoming(fy));
    case "rental":
      return renderRental(await api.spreadsheetRental(fy));
    case "work-trips":
      return renderWorkTrips(await api.spreadsheetWorkTrips(fy));
  }
}

function fmt(val: number): string {
  return Math.abs(val).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function renderOutgoing(data: SpreadsheetOutgoing[]) {
  const el = document.getElementById("ss-outgoing-content")!;
  const totalDebit = data.reduce((s, t) => s + Math.abs(t.amount), 0);
  const totalZD = data.reduce((s, t) => s + Math.abs(t.biz_amount), 0);

  el.innerHTML = `
    <div class="ss-summary">
      <span>${data.length} transactions</span>
      <span>Total: $${fmt(totalDebit)}</span>
      <span>Biz Total: $${fmt(totalZD)}</span>
    </div>
    <div class="ss-table-wrap">
      <table class="tax-table ss-full-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Account</th>
            <th>Description</th>
            <th>Category</th>
            <th>Debit</th>
            <th>Biz%</th>
            <th>Biz$</th>
          </tr>
        </thead>
        <tbody>
          ${data.map((t) => `
            <tr>
              <td>${t.date}</td>
              <td>${escapeHtml(t.account_name)}</td>
              <td>${escapeHtml(t.description.substring(0, 50))}</td>
              <td>${escapeHtml(t.category_name || "Uncategorized")}</td>
              <td class="negative">$${fmt(t.amount)}</td>
              <td>${t.biz_pct > 0 ? `${t.biz_pct}%` : ""}</td>
              <td>${t.biz_amount !== 0 ? `<span class="negative">$${fmt(t.biz_amount)}</span>` : ""}</td>
            </tr>
          `).join("")}
          <tr class="tax-total">
            <td colspan="4">Total</td>
            <td class="negative">$${fmt(totalDebit)}</td>
            <td></td>
            <td class="negative">$${fmt(totalZD)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  `;
}

function renderIncoming(data: SpreadsheetIncoming[]) {
  const el = document.getElementById("ss-incoming-content")!;

  // Group by category
  const groups: Record<string, { total: number; items: SpreadsheetIncoming[] }> = {};
  for (const t of data) {
    const cat = t.category_name || "Uncategorized";
    if (!groups[cat]) groups[cat] = { total: 0, items: [] };
    groups[cat].total += t.amount;
    groups[cat].items.push(t);
  }

  const totalIncome = data.reduce((s, t) => s + t.amount, 0);

  el.innerHTML = `
    <div class="ss-summary">
      <span>${data.length} transactions</span>
      <span>Total Income: $${fmt(totalIncome)}</span>
    </div>
    <div class="tax-section">
      <h3>Summary by Category</h3>
      <table class="tax-table">
        <thead><tr><th>Category</th><th>Count</th><th>Total</th></tr></thead>
        <tbody>
          ${Object.entries(groups).sort((a, b) => b[1].total - a[1].total).map(([cat, g]) => `
            <tr>
              <td>${escapeHtml(cat)}</td>
              <td>${g.items.length}</td>
              <td class="positive">$${fmt(g.total)}</td>
            </tr>
          `).join("")}
          <tr class="tax-total"><td>Total</td><td>${data.length}</td><td class="positive">$${fmt(totalIncome)}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="ss-table-wrap">
      <table class="tax-table ss-full-table">
        <thead>
          <tr><th>Date</th><th>Account</th><th>Description</th><th>Category</th><th>Amount</th></tr>
        </thead>
        <tbody>
          ${data.map((t) => `
            <tr>
              <td>${t.date}</td>
              <td>${escapeHtml(t.account_name)}</td>
              <td>${escapeHtml(t.description.substring(0, 50))}</td>
              <td>${escapeHtml(t.category_name || "Uncategorized")}</td>
              <td class="positive">$${fmt(t.amount)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderRental(data: RentalProperty[]) {
  const el = document.getElementById("ss-rental-content")!;

  if (!data.length) {
    el.innerHTML = "<p>No rental properties configured.</p>";
    return;
  }

  el.innerHTML = data.map((prop) => `
    <div class="tax-section">
      <h3>${escapeHtml(prop.name)} - ${escapeHtml(prop.address)}</h3>
      <p class="tax-hint">Ownership: ${prop.ownership_pct}% | Rental weeks: ${prop.rental_weeks}</p>

      <table class="tax-table">
        <thead><tr><th>Item</th><th>Gross</th><th>Your Share (${prop.ownership_pct}%)</th></tr></thead>
        <tbody>
          <tr>
            <td>Rental income</td>
            <td class="positive">$${fmt(prop.gross_income)}</td>
            <td class="positive">$${fmt(prop.income_share)}</td>
          </tr>
          <tr class="tax-total"><td colspan="3" style="padding-top:0.75rem"><strong>Expenses</strong></td></tr>
          ${prop.expenses.map((e) => `
            <tr>
              <td>${escapeHtml(e.ato_label)}</td>
              <td class="negative">$${fmt(e.raw_amount)}</td>
              <td class="negative">$${fmt(e.share_amount)}</td>
            </tr>
          `).join("")}
          ${prop.depreciation.map((d) => `
            <tr>
              <td>${escapeHtml(d.description)}</td>
              <td></td>
              <td class="negative">$${fmt(d.amount)}</td>
            </tr>
          `).join("")}
          <tr class="tax-total">
            <td>Total Expenses</td>
            <td></td>
            <td class="negative">$${fmt(prop.total_expenses)}</td>
          </tr>
          <tr class="tax-total">
            <td>Net Rent</td>
            <td></td>
            <td class="${prop.net_rent >= 0 ? "positive" : "negative"}">${prop.net_rent < 0 ? "-" : ""}$${fmt(prop.net_rent)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  `).join("");
}

function renderWorkTrips(data: WorkTripsResponse) {
  const el = document.getElementById("ss-work-trips-content")!;

  const tripsHtml = data.trips.length ? data.trips.map((trip) => `
    <div class="tax-section">
      <h3>${escapeHtml(trip.name)}</h3>
      <p class="tax-hint">${trip.start_date} to ${trip.end_date}</p>
      <table class="tax-table">
        <thead><tr><th>Type</th><th>Amount</th><th>Description</th></tr></thead>
        <tbody>
          ${trip.expenses.map((e) => `
            <tr>
              <td>${escapeHtml(e.expense_type)}</td>
              <td class="negative">$${fmt(e.amount)}</td>
              <td>${escapeHtml(e.description || "")}</td>
            </tr>
          `).join("")}
          <tr class="tax-total"><td>Total</td><td class="negative">$${fmt(trip.total)}</td><td></td></tr>
        </tbody>
      </table>
    </div>
  `).join("") : "<p>No work trips recorded.</p>";

  el.innerHTML = `
    ${tripsHtml}
    <div class="tax-section">
      <h3>Working from Home</h3>
      <p class="tax-hint">ATO fixed rate method: $${data.wfh.rate_per_hour}/hour</p>
      <table class="tax-table">
        <thead><tr><th>Item</th><th>Value</th></tr></thead>
        <tbody>
          <tr><td>Weeks worked from home</td><td>${data.wfh.weeks}</td></tr>
          <tr><td>WFH allocation</td><td>${data.wfh.allocation_pct}%</td></tr>
          <tr><td>Calculated hours</td><td>${data.wfh.hours.toFixed(1)}</td></tr>
          <tr class="tax-total"><td>WFH Deduction</td><td class="negative">$${fmt(data.wfh.amount)}</td></tr>
        </tbody>
      </table>
    </div>
  `;
}
