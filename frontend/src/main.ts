import { api, type Transaction, type Category, type AccountSummary, type Holding, type TaxSummary, type ATOReturn, type ATOLodgedResponse, type ATOTaxpayer, type ATOLabelRow, type DepreciationResponse, type SharedExpenseItem, type SharedExpensesResponse, type EconomicsSummary } from "./api";
import { renderMonthlyChart, renderCategoryChart, renderTrendsChart, renderTaxBreakdownChart, renderCpiHistoryChart } from "./charts";
import { populateFilters, getTransactionFilters } from "./filters";
import { initSpreadsheet, loadSpreadsheet } from "./spreadsheet";
import "./style.css";

let allCategories: Category[] = [];

// --- Tab navigation ---

const VALID_VIEWS = new Set([
  "dashboard", "transactions", "budget", "trends", "year-review",
  "financial-year", "shared-expenses", "recurring", "tax", "lodgement", "depreciation", "economics",
]);

function activateTab(viewId: string) {
  if (!VALID_VIEWS.has(viewId)) viewId = "dashboard";
  document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.querySelector(`.tab[data-tab="${viewId}"]`)?.classList.add("active");
  document.getElementById(viewId)?.classList.add("active");
  loadView(viewId);
}

document.querySelectorAll<HTMLButtonElement>(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const viewId = btn.dataset.tab!;
    // Setting the hash drives activation via the hashchange listener so the URL
    // stays in sync and a refresh reopens the same tab. Re-activate directly if
    // the hash is already this tab (no hashchange event would fire).
    if (location.hash.slice(1) === viewId) activateTab(viewId);
    else location.hash = viewId;
  });
});

window.addEventListener("hashchange", () => activateTab(location.hash.slice(1)));

// --- View loaders ---

async function loadView(view: string) {
  switch (view) {
    case "dashboard":
      return loadDashboard();
    case "transactions":
      return loadTransactions();
    case "budget":
      return loadBudget();
    case "trends":
      return loadTrends();
    case "year-review":
      return loadYearReview();
    case "financial-year":
      return loadSpreadsheet();
    case "shared-expenses":
      return loadSharedExpenses();
    case "recurring":
      return loadRecurring();
    case "tax":
      return loadTax();
    case "lodgement":
      return loadLodgement();
    case "depreciation":
      return loadDepreciation();
    case "economics":
      return loadEconomics();
  }
}

function getDashboardFilterParams(): Record<string, string> {
  const params: Record<string, string> = {};
  const excludeLoans = (document.getElementById("dash-exclude-loans") as HTMLInputElement)?.checked;
  const excludeTransfers = (document.getElementById("dash-exclude-transfers") as HTMLInputElement)?.checked;
  params.exclude_loans = excludeLoans ? "true" : "false";
  params.exclude_transfers = excludeTransfers ? "true" : "false";
  return params;
}

async function loadDashboard() {
  const year = (document.getElementById("dash-year") as HTMLSelectElement)?.value ||
    String(new Date().getFullYear());

  const filterParams = getDashboardFilterParams();

  const [monthly, categories, accountsData] = await Promise.all([
    api.monthlySummary(year, filterParams),
    api.categorySummary(`${year}-01-01`, `${year}-12-31`, filterParams),
    api.accountsSummary(),
  ]);

  renderNetWorthPanel(accountsData.accounts, accountsData.holdings);

  renderMonthlyChart(
    document.getElementById("chart-monthly") as HTMLCanvasElement,
    monthly
  );
  renderCategoryChart(
    document.getElementById("chart-category") as HTMLCanvasElement,
    categories
  );

  // Summary cards
  const totalIncome = monthly.reduce((s, m) => s + m.income, 0);
  const totalExpenses = monthly.reduce((s, m) => s + Math.abs(m.expenses), 0);
  const net = totalIncome - totalExpenses;
  const savingsRate = totalIncome > 0 ? (net / totalIncome) * 100 : 0;
  const avgMonthlyExpense = totalExpenses / (monthly.length || 1);

  const container = document.getElementById("summary-cards")!;
  container.innerHTML = `
    <div class="card income">
      <div class="card-label">Total Income</div>
      <div class="card-value">$${fmt(totalIncome)}</div>
    </div>
    <div class="card expense">
      <div class="card-label">Total Expenses</div>
      <div class="card-value">$${fmt(totalExpenses)}</div>
    </div>
    <div class="card ${net >= 0 ? "income" : "expense"}">
      <div class="card-label">Net</div>
      <div class="card-value">${net < 0 ? "-" : ""}$${fmt(net)}</div>
    </div>
    <div class="card">
      <div class="card-label">Savings Rate</div>
      <div class="card-value ${savingsRate >= 0 ? "positive" : "negative"}">${savingsRate.toFixed(1)}%</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Monthly Spend</div>
      <div class="card-value">$${fmt(avgMonthlyExpense)}</div>
    </div>
  `;

  // Category breakdown table
  const expenses = categories.filter((c) => c.total < 0 && c.category !== "Uncategorized");
  const breakdownEl = document.getElementById("category-breakdown")!;
  const totalCatExpenses = expenses.reduce((s, c) => s + Math.abs(c.total), 0);
  breakdownEl.innerHTML = `
    <h3>Spending by Category</h3>
    <table class="mini-table">
      <thead><tr><th>Category</th><th>Transactions</th><th>Total</th><th>%</th></tr></thead>
      <tbody>
        ${expenses.sort((a, b) => a.total - b.total).map((c) => {
          const pct = totalCatExpenses > 0 ? (Math.abs(c.total) / totalCatExpenses * 100) : 0;
          return `
          <tr>
            <td>${escapeHtml(c.category || "Uncategorized")}</td>
            <td>${c.count}</td>
            <td class="negative">$${fmt(Math.abs(c.total))}</td>
            <td>${pct.toFixed(1)}%</td>
          </tr>`;
        }).join("")}
        <tr class="tax-total">
          <td>Total</td><td></td>
          <td class="negative">$${fmt(totalCatExpenses)}</td>
          <td></td>
        </tr>
      </tbody>
    </table>
  `;
}

function renderNetWorthPanel(accounts: AccountSummary[], holdings: Holding[]) {
  const panel = document.getElementById("net-worth-panel")!;

  // Account groups
  const groups: Record<string, { label: string; accounts: AccountSummary[] }> = {
    checking: { label: "Cash", accounts: [] },
    savings: { label: "Savings", accounts: [] },
    loan: { label: "Debt", accounts: [] },
    credit: { label: "Credit", accounts: [] },
    other: { label: "Other", accounts: [] },
  };

  for (const acct of accounts) {
    const group = groups[acct.account_type] || groups.other;
    group.accounts.push(acct);
  }

  // Holdings groups
  const holdingGroups: Record<string, { label: string; items: Holding[] }> = {
    property: { label: "Property", items: [] },
    vehicle: { label: "Vehicles", items: [] },
    shares: { label: "Shares", items: [] },
    super: { label: "Super", items: [] },
    crypto: { label: "Crypto", items: [] },
  };

  for (const h of holdings) {
    const group = holdingGroups[h.asset_type];
    if (group) group.items.push(h);
  }

  const totalCash = accounts
    .filter((a) => a.account_type !== "loan" && a.account_type !== "credit")
    .reduce((s, a) => s + a.balance, 0);
  const totalDebt = accounts
    .filter((a) => a.account_type === "loan" || a.account_type === "credit")
    .reduce((s, a) => s + a.balance, 0);
  const totalHoldings = holdings.reduce((s, h) => s + (h.current_value || 0), 0);
  const netWorth = totalCash + totalDebt + totalHoldings;

  const accountGroupHtml = Object.entries(groups)
    .filter(([, g]) => g.accounts.length > 0)
    .map(([type, g]) => {
      const groupTotal = g.accounts.reduce((s, a) => s + a.balance, 0);
      const isDebt = type === "loan" || type === "credit";
      return `
        <div class="nw-group">
          <div class="nw-group-header">
            <span class="nw-group-label">${g.label}</span>
            <span class="nw-group-total ${isDebt ? "negative" : "positive"}">$${fmt(groupTotal)}</span>
          </div>
          ${g.accounts.map((a) => `
            <div class="nw-account">
              <span class="nw-account-name">${escapeHtml(a.name)}</span>
              <span class="nw-account-balance ${a.balance < 0 ? "negative" : "positive"}">$${fmt(a.balance)}</span>
            </div>
          `).join("")}
        </div>
      `;
    })
    .join("");

  const holdingGroupHtml = Object.entries(holdingGroups)
    .filter(([, g]) => g.items.length > 0)
    .map(([, g]) => {
      const groupTotal = g.items.reduce((s, h) => s + (h.current_value || 0), 0);
      return `
        <div class="nw-group">
          <div class="nw-group-header">
            <span class="nw-group-label">${g.label}</span>
            <span class="nw-group-total positive">$${fmt(groupTotal)}</span>
          </div>
          ${g.items.map((h) => `
            <div class="nw-account">
              <span class="nw-account-name">${escapeHtml(h.name)}${h.ticker ? ` (${h.ticker})` : ""}${h.units ? ` x${h.units}` : ""}</span>
              <span class="nw-account-balance positive">$${fmt(h.current_value || 0)}</span>
            </div>
          `).join("")}
        </div>
      `;
    })
    .join("");

  panel.innerHTML = `
    <div class="net-worth-card">
      <div class="nw-header">
        <h3>Net Worth</h3>
        <span class="nw-total ${netWorth >= 0 ? "positive" : "negative"}">${netWorth < 0 ? "-" : ""}$${fmt(netWorth)}</span>
      </div>
      <div class="nw-groups">${accountGroupHtml}${holdingGroupHtml}</div>
    </div>
  `;
}

// --- ATO Tax Tab ---

async function loadTax() {
  const fy = (document.getElementById("tax-fy") as HTMLSelectElement)?.value;
  const data = await api.atoReturn(fy);
  renderATOReturn(data);
}

function renderATOReturn(data: ATOReturn) {
  const el = document.getElementById("tax-summary")!;

  const rentalNet = data.rental.reduce((s, r) => s + r.net_rent, 0);
  const bizNet = data.business.reduce((s, b) => s + b.net, 0);
  const tripTotal = data.deductions.work_trips.reduce((s, t) => s + t.total, 0);
  const wfhAmount = data.deductions.wfh.amount;

  const s = data.summary;
  const refundClass = s.refund_or_payable >= 0 ? "income" : "expense";
  const refundLabel = s.refund_or_payable >= 0 ? "Estimated Refund" : "Estimated Bill";

  el.innerHTML = `
    <div class="tax-header">
      <h2>${escapeHtml(data.fy_label)}</h2>
      <span class="tax-dates">Australian Individual Tax Return</span>
    </div>

    <!-- BOTTOM-LINE SUMMARY -->
    <div class="tax-cards">
      <div class="card ${refundClass}" style="grid-column: span 2;">
        <div class="card-label">${refundLabel}</div>
        <div class="card-value" style="font-size: 1.6rem;">
          ${s.refund_or_payable < 0 ? "-" : ""}$${fmt(Math.abs(s.refund_or_payable))}
        </div>
      </div>
      <div class="card">
        <div class="card-label">Taxable income</div>
        <div class="card-value">$${fmt(s.taxable_income)}</div>
      </div>
      <div class="card">
        <div class="card-label">Effective rate</div>
        <div class="card-value">${s.effective_rate.toFixed(1)}%</div>
      </div>
    </div>

    <!-- Reconciliation table -->
    <div class="tax-section">
      <h3>Tax Calculation Summary</h3>
      <table class="tax-table">
        <tbody>
          <tr><td>Assessable income (salary + interest + net rent/biz if profit)</td>
              <td class="positive">$${fmt(s.assessable_income)}</td></tr>
          <tr><td>Less: total deductions (rental loss + business loss + WFH + work trips)</td>
              <td class="negative">-$${fmt(s.total_deductions)}</td></tr>
          <tr class="tax-total"><td>Taxable income</td>
              <td>$${fmt(s.taxable_income)}</td></tr>
          <tr><td>PAYG tax</td><td>$${fmt(s.payg)}</td></tr>
          <tr><td>Medicare levy (2%)</td><td>$${fmt(s.medicare)}</td></tr>
          <tr class="tax-total"><td>Total tax payable</td><td>$${fmt(s.total_tax)}</td></tr>
          <tr><td>Less: tax already withheld (PAYG summary)</td>
              <td class="negative">-$${fmt(s.tax_withheld)}</td></tr>
          <tr class="tax-total">
            <td>${s.refund_or_payable >= 0 ? "Refund" : "Amount owing"}</td>
            <td class="${refundClass === 'income' ? 'positive' : 'negative'}">
              ${s.refund_or_payable < 0 ? "-" : ""}$${fmt(Math.abs(s.refund_or_payable))}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="tax-cards">
      <div class="card income">
        <div class="card-label">Salary (Item 1)</div>
        <div class="card-value">$${fmt(data.income.salary)}</div>
      </div>
      <div class="card">
        <div class="card-label">Tax Withheld</div>
        <div class="card-value">$${fmt(data.income.tax_withheld)}</div>
      </div>
      <div class="card ${rentalNet >= 0 ? "income" : "expense"}">
        <div class="card-label">Net Rent (Item 21)</div>
        <div class="card-value">${rentalNet < 0 ? "-" : ""}$${fmt(rentalNet)}</div>
      </div>
      <div class="card ${bizNet >= 0 ? "income" : "expense"}">
        <div class="card-label">Business Net</div>
        <div class="card-value">${bizNet < 0 ? "-" : ""}$${fmt(bizNet)}</div>
      </div>
    </div>

    <!-- Item 1: Salary -->
    <div class="tax-section">
      <h3>Item 1: Salary or Wages</h3>
      <table class="tax-table">
        <tbody>
          <tr><td>Gross salary</td><td class="positive">$${fmt(data.income.salary)}</td></tr>
          <tr><td>Tax withheld</td><td>$${fmt(data.income.tax_withheld)}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Item 10: Interest -->
    <div class="tax-section">
      <h3>Item 10: Interest</h3>
      <table class="tax-table">
        <tbody>
          <tr><td>Interest income</td><td class="positive">$${fmt(data.income.interest)}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Item 21: Rental -->
    ${data.rental.map((r) => `
      <div class="tax-section">
        <h3>Item 21: Rent - ${escapeHtml(r.property)}</h3>
        <p class="tax-hint">${escapeHtml(r.address)} | ${r.ownership_pct}% ownership · ${r.floor_area_pct}% floor area · ${r.rental_weeks} weeks rented</p>
        <table class="tax-table">
          <thead><tr>
            <th>Line item</th><th>Gross</th><th>Apply</th><th>Factor</th><th>Your share</th>
          </tr></thead>
          <tbody>
            <tr>
              <td>Gross rental income</td>
              <td class="positive">$${fmt(r.gross_income)}</td>
              <td>ownership</td>
              <td>${(r.ownership_pct/100).toFixed(2)}</td>
              <td class="positive">$${fmt(r.income_share)}</td>
            </tr>
            ${r.expenses.map((e) => `
              <tr>
                <td>${escapeHtml(e.ato_label)}</td>
                <td>$${fmt(e.raw)}</td>
                <td class="tax-hint">${e.apply.length ? e.apply.join("+") : "100%"}</td>
                <td>${e.factor.toFixed(4)}</td>
                <td class="negative">-$${fmt(e.share)}</td>
              </tr>
            `).join("")}
            ${r.depreciation > 0 ? `
              <tr>
                <td>Capital allowances (QS depreciation)</td>
                <td>$${fmt(r.depreciation)}</td>
                <td class="tax-hint">depreciation</td>
                <td>1.0000</td>
                <td class="negative">-$${fmt(r.depreciation)}</td>
              </tr>
            ` : ""}
            <tr class="tax-total">
              <td colspan="4">Net rent</td>
              <td class="${r.net_rent >= 0 ? "positive" : "negative"}">${r.net_rent < 0 ? "-" : ""}$${fmt(r.net_rent)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    `).join("")}

    <!-- Business Schedule -->
    ${data.business.map((b) => `
      <div class="tax-section">
        <h3>Business: ${escapeHtml(b.name)}</h3>
        <p class="tax-hint">ABN: ${b.abn}</p>
        <table class="tax-table">
          <thead><tr><th>P&L Item</th><th>Amount</th></tr></thead>
          <tbody>
            <tr><td>Business income</td><td class="positive">$${fmt(b.income)}</td></tr>
            <tr><td>COGS / Expenses</td><td class="negative">$${fmt(Math.abs(b.expenses))}</td></tr>
            ${b.depreciation > 0 ? `<tr><td>Depreciation</td><td class="negative">-$${fmt(b.depreciation)}</td></tr>` : ""}
            <tr class="tax-total">
              <td>Net business ${b.net >= 0 ? "income" : "loss"}</td>
              <td class="${b.net >= 0 ? "positive" : "negative"}">${b.net < 0 ? "-" : ""}$${fmt(b.net)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    `).join("")}

    <!-- Deductions -->
    <div class="tax-section">
      <h3>D5: Other Work-Related Deductions</h3>

      ${data.deductions.work_trips.length > 0 ? `
        <h4 style="margin: 0.5rem 0 0.25rem; font-size: 0.85rem;">Work Travel</h4>
        ${data.deductions.work_trips.map((t) => `
          <p class="tax-hint">${escapeHtml(t.name)} (${t.start_date} to ${t.end_date})</p>
          <table class="tax-table" style="margin-bottom: 0.5rem;">
            <tbody>
              ${Object.entries(t.expenses).map(([type, amt]) => `
                <tr><td>${escapeHtml(type)}</td><td class="negative">-$${fmt(amt as number)}</td></tr>
              `).join("")}
              <tr class="tax-total"><td>Trip total</td><td class="negative">-$${fmt(t.total)}</td></tr>
            </tbody>
          </table>
        `).join("")}
      ` : ""}

      <h4 style="margin: 0.5rem 0 0.25rem; font-size: 0.85rem;">Working from Home</h4>
      <table class="tax-table">
        <tbody>
          <tr><td>${data.deductions.wfh.weeks} weeks x ${data.deductions.wfh.allocation_pct}%</td><td class="negative">-$${fmt(wfhAmount)}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Spouse -->
    ${data.spouse?.name ? `
      <div class="tax-section">
        <h3>Spouse Details</h3>
        <table class="tax-table">
          <tbody>
            <tr><td>Name</td><td>${escapeHtml(data.spouse.name)}</td></tr>
            <tr><td>Taxable income</td><td>$${fmt(data.spouse.taxable_income)}</td></tr>
          </tbody>
        </table>
      </div>
    ` : ""}
  `;
}

// --- Lodgement Tab (archived lodged ATO returns) ---

let lodgedData: ATOLodgedResponse | null = null;

async function loadLodgement() {
  if (!lodgedData) {
    lodgedData = await api.atoLodged();
    // Populate the taxpayer selector (hidden when there's only one).
    const tpSel = document.getElementById("lodgement-taxpayer") as HTMLSelectElement | null;
    if (tpSel && tpSel.options.length === 0) {
      for (const tp of lodgedData.taxpayers) {
        const opt = document.createElement("option");
        opt.value = tp.id;
        opt.textContent = tp.name;
        tpSel.appendChild(opt);
      }
      tpSel.style.display = lodgedData.taxpayers.length > 1 ? "" : "none";
    }
    syncLodgementFYOptions();
  }
  renderLodgement();
}

function currentTaxpayer(): ATOTaxpayer | null {
  if (!lodgedData || !lodgedData.taxpayers.length) return null;
  const id = (document.getElementById("lodgement-taxpayer") as HTMLSelectElement | null)?.value;
  return lodgedData.taxpayers.find((t) => t.id === id) ?? lodgedData.taxpayers[0];
}

// Rebuild the FY selector to match the selected taxpayer's lodged years.
function syncLodgementFYOptions() {
  const sel = document.getElementById("lodgement-fy") as HTMLSelectElement | null;
  const tp = currentTaxpayer();
  if (!sel || !tp) return;
  sel.innerHTML = "";
  for (const y of tp.lodged) {
    const opt = document.createElement("option");
    opt.value = String(y.fy);
    opt.textContent = y.fy_label;
    sel.appendChild(opt);
  }
}

// What gets written to the clipboard for a label value: a plain number string
// (no $, no commas) or the raw string for Y/N style answers.
function copyValue(v: number | string): string {
  return String(v);
}

function displayValue(v: number | string): string {
  if (typeof v !== "number") return escapeHtml(v);
  return `${v < 0 ? "-" : ""}$${fmt(Math.abs(v))}`;
}

// A click-to-copy button. `copy` is the exact clipboard text; `display` the
// already-escaped/formatted visible HTML.
function copyButton(copy: string, display: string, extraClass = ""): string {
  const esc = escapeHtml(copy);
  return `<button class="lodge-copy ${extraClass}" data-copy="${esc}" title="Copy ${esc}">${display}</button>`;
}

// "FY 2024-25" from the FY-ending year (2025).
function fyLabel(fy: number): string {
  return `FY ${fy - 1}-${String(fy).slice(2)}`;
}

// Delegated click-to-copy for any `.lodge-copy` button inside a container.
// On success the button flashes "✓ copied"; on failure (insecure context /
// denied permission) it flashes "⚠ copy failed" so the click isn't silent.
function attachCopyHandler(containerId: string) {
  document.getElementById(containerId)?.addEventListener("click", async (e) => {
    const btn = (e.target as HTMLElement).closest(".lodge-copy") as HTMLButtonElement | null;
    if (!btn) return;
    const prev = btn.textContent;
    const flash = (msg: string) => {
      btn.classList.add("copied");
      btn.textContent = msg;
      setTimeout(() => { btn.textContent = prev; btn.classList.remove("copied"); }, 900);
    };
    try {
      await navigator.clipboard.writeText(btn.dataset.copy || "");
      flash("✓ copied");
    } catch {
      flash("⚠ copy failed");
    }
  });
}

function labelRowsHtml(rows: ATOLabelRow[]): string {
  return rows.map((r) => {
    // Section rows carry `code`; carry-forward rows carry `label` — accept either.
    const code = r.code ?? r.label ?? "";
    const neg = typeof r.value === "number" && r.value < 0;
    return `
    <tr>
      <td class="lodge-code">${escapeHtml(code)}</td>
      <td>${escapeHtml(r.desc)}</td>
      <td class="lodge-val ${neg ? "negative" : ""}">
        ${copyButton(copyValue(r.value), displayValue(r.value))}
      </td>
    </tr>`;
  }).join("");
}

function renderLodgement() {
  const el = document.getElementById("lodgement-content")!;
  const tp = currentTaxpayer();

  if (!tp || !tp.lodged.length) {
    el.innerHTML = `<p class="tax-hint">No lodged returns on file. Add them to
      <code>config/ato_returns.yaml</code> (see the <code>.example</code> template).</p>`;
    return;
  }

  const sel = document.getElementById("lodgement-fy") as HTMLSelectElement | null;
  const selFy = sel?.value ? Number(sel.value) : tp.lodged[0].fy;
  const year = tp.lodged.find((y) => y.fy === selFy) ?? tp.lodged[0];

  const ref = tp.reference || {};
  const cf = tp.latest_carry_forward || [];

  el.innerHTML = `
    ${cf.length ? `
      <div class="tax-section lodge-carry">
        <h3>⤳ Carry-forward into your next return</h3>
        <p class="tax-hint">From ${escapeHtml(cf[0].from_fy_label)} — apply these when preparing the following year.</p>
        <table class="tax-table">
          <tbody>${labelRowsHtml(cf)}</tbody>
        </table>
      </div>
    ` : ""}

    <div class="tax-section">
      <h3>Reference (same every year)</h3>
      <table class="tax-table">
        <tbody>
          ${ref.tfn ? `<tr><td>TFN</td><td class="lodge-val"><button class="lodge-copy" data-copy="${escapeHtml((ref.tfn||"").replace(/\s/g,""))}">${escapeHtml(ref.tfn)}</button></td></tr>` : ""}
          ${ref.abn ? `<tr><td>ABN (${escapeHtml(ref.business_name||"")})</td><td class="lodge-val"><button class="lodge-copy" data-copy="${escapeHtml(ref.abn)}">${escapeHtml(ref.abn)}</button></td></tr>` : ""}
          ${ref.occupation ? `<tr><td>Occupation</td><td>${escapeHtml(ref.occupation)}</td></tr>` : ""}
          ${ref.health_insurer?.id ? `<tr><td>Private health</td><td>${escapeHtml(ref.health_insurer.id)} · membership ${escapeHtml(ref.health_insurer.membership||"")}</td></tr>` : ""}
          ${ref.spouse?.name ? `<tr><td>Spouse</td><td>${escapeHtml(ref.spouse.name)} (DOB ${escapeHtml(ref.spouse.date_of_birth||"")})</td></tr>` : ""}
        </tbody>
      </table>
    </div>

    <div class="tax-header">
      <h2>${escapeHtml(year.fy_label)} — as lodged</h2>
      <span class="tax-dates">${year.receipt ? "ATO receipt " + escapeHtml(year.receipt) : ""}</span>
    </div>

    <div class="tax-cards">
      <div class="card income">
        <div class="card-label">Taxable income</div>
        <div class="card-value">$${fmt(year.taxable_income)}</div>
      </div>
      <div class="card">
        <div class="card-label">Tax withheld</div>
        <div class="card-value">$${fmt(year.tax_withheld)}</div>
      </div>
    </div>

    ${year.sections.map((sec) => `
      <div class="tax-section">
        <h3>${escapeHtml(sec.name)}</h3>
        <table class="tax-table lodge-table">
          <thead><tr><th>Label</th><th>Item</th><th>Value</th></tr></thead>
          <tbody>${labelRowsHtml(sec.rows)}</tbody>
        </table>
      </div>
    `).join("")}
  `;
}

attachCopyHandler("lodgement-content");

document.getElementById("lodgement-taxpayer")?.addEventListener("change", () => {
  syncLodgementFYOptions();
  renderLodgement();
});

document.getElementById("lodgement-fy")?.addEventListener("change", () => {
  renderLodgement();
});

// --- Depreciation Tab (asset register / WDV roll-forward) ---

let depreciationData: DepreciationResponse | null = null;

async function loadDepreciation() {
  if (!depreciationData) {
    depreciationData = await api.depreciation();
    // FY selector = the union of every FY any asset has a row for, newest first.
    const sel = document.getElementById("depreciation-fy") as HTMLSelectElement | null;
    if (sel && sel.options.length === 0) {
      const fys = new Set<number>();
      for (const reg of depreciationData.registers)
        for (const k of Object.keys(reg.totals)) fys.add(Number(k));
      const sorted = [...fys].sort((a, b) => b - a);
      for (const fy of sorted) {
        const opt = document.createElement("option");
        opt.value = String(fy);
        opt.textContent = fyLabel(fy);
        sel.appendChild(opt);
      }
      // Default to most recent complete FY (matches the Lodgement default).
      const now = new Date();
      const currentFY = now.getMonth() >= 6 ? now.getFullYear() + 1 : now.getFullYear();
      if (sorted.includes(currentFY - 1)) sel.value = String(currentFY - 1);
    }
  }
  renderDepreciation();
}

// Copy cells carry the exact 2-decimal figure (matching the displayed cents and
// the Lodgement tab), so a column of copies sums to the printed total.
function deprMoney(n: number): string {
  return copyButton(n.toFixed(2), `$${fmt(n)}`);
}

function renderDepreciation() {
  const el = document.getElementById("depreciation-content")!;
  const data = depreciationData;
  if (!data || !data.registers.length || !Object.keys(data.fy_totals).length) {
    el.innerHTML = `<p class="tax-hint">No depreciation register on file. Add assets to
      <code>config/depreciation.yaml</code> (see the <code>.example</code> template).</p>`;
    return;
  }

  const sel = document.getElementById("depreciation-fy") as HTMLSelectElement | null;
  const fy = sel?.value ? Number(sel.value) : Math.max(...Object.keys(data.fy_totals).map(Number));

  el.innerHTML = data.registers.map((reg) => {
    const t = reg.totals[String(fy)];
    const coOwned = reg.ownership_pct < 100;
    const share = reg.ownership_pct / 100;
    const shareCol = coOwned ? `<th>Your ${reg.ownership_pct}%</th>` : "";
    const cols = coOwned ? 7 : 6;

    // One row per asset: its opening → decline → (your share) → closing for the FY.
    const assetRows = reg.assets.map((a) => {
      const y = a.years.find((yr) => yr.fy === fy);
      if (!y) {
        return `<tr>
          <td>${escapeHtml(a.description)}</td>
          <td class="tax-hint" colspan="${cols - 1}">not held in this FY (acquired ${escapeHtml(a.acquired)})</td>
        </tr>`;
      }
      const shareCell = coOwned ? `<td class="lodge-val">${deprMoney(y.deductible * share)}</td>` : "";
      const fullDeductible = coOwned
        ? `<td>$${fmt(y.deductible)}</td>`
        : `<td class="lodge-val">${deprMoney(y.deductible)}</td>`;
      return `<tr>
        <td>${escapeHtml(a.description)}</td>
        <td>$${fmt(y.opening)}</td>
        <td class="negative">-$${fmt(y.decline)}</td>
        ${fullDeductible}
        ${shareCell}
        <td>$${fmt(y.closing)}</td>
        <td class="tax-hint">${escapeHtml(a.acquired)} · ${a.effective_life}y · ${escapeHtml(a.method)}</td>
      </tr>`;
    }).join("");

    // What goes on the return: the taxpayer's share when co-owned, else the full amount.
    const claimable = t ? (coOwned ? t.taxpayer_deductible : t.deductible) : 0;
    const fullTotal = t ? t.deductible : 0;

    return `
      <div class="tax-header">
        <h2>${escapeHtml(reg.owner)}</h2>
        <span class="tax-dates">${escapeHtml(reg.kind)}${coOwned ? ` · ${reg.ownership_pct}% owned` : ""}${reg.method_note ? " · " + escapeHtml(reg.method_note) : ""}</span>
      </div>
      <div class="tax-cards">
        <div class="card expense">
          <div class="card-label">${coOwned ? `Your ${reg.ownership_pct}% deductible` : "Deductible decline"} · ${fyLabel(fy)}</div>
          <div class="card-value">${deprMoney(claimable)}</div>
          ${coOwned ? `<div class="card-label">full-property: $${fmt(fullTotal)}</div>` : ""}
        </div>
        <div class="card">
          <div class="card-label">Assets held</div>
          <div class="card-value">${t ? t.n_assets : 0}</div>
        </div>
      </div>
      <div class="tax-section">
        <div class="depr-scroll">
        <table class="tax-table depr-table">
          <thead><tr>
            <th>Asset</th><th>Opening WDV</th><th>Decline</th><th>Deductible</th>${shareCol}<th>Closing WDV</th><th>Detail</th>
          </tr></thead>
          <tbody>${assetRows}</tbody>
          <tfoot><tr class="tax-total">
            <td>Total</td><td></td><td></td>
            <td${coOwned ? "" : ' class="lodge-val"'}>${coOwned ? `$${fmt(fullTotal)}` : deprMoney(fullTotal)}</td>
            ${coOwned ? `<td class="lodge-val">${deprMoney(claimable)}</td>` : ""}
            <td></td><td></td>
          </tr></tfoot>
        </table>
        </div>
      </div>`;
  }).join("");
}

attachCopyHandler("depreciation-content");

document.getElementById("depreciation-fy")?.addEventListener("change", () => {
  renderDepreciation();
});

// --- Year Review Tab ---

async function loadYearReview() {
  const year = (document.getElementById("review-year") as HTMLSelectElement)?.value || "2025";
  const data = await api.yearReview(year);
  renderYearReview(data);
}

function renderYearReview(data: import("./api").YearReview) {
  const el = document.getElementById("year-review-content")!;

  const prevIncome = data.previous_year?.income || 0;
  const prevExpenses = Math.abs(data.previous_year?.expenses || 0);
  const incomeChange = prevIncome > 0 ? ((data.total_income - prevIncome) / prevIncome * 100) : 0;
  const expenseChange = prevExpenses > 0 ? ((data.total_expenses - prevExpenses) / prevExpenses * 100) : 0;

  const expenseCategories = data.categories.filter(c => c.total < 0 && c.category !== "Uncategorized");
  const totalCatExpenses = expenseCategories.reduce((s, c) => s + Math.abs(c.total), 0);

  el.innerHTML = `
    <h2>${data.year} Year in Review</h2>

    <div class="tax-cards">
      <div class="card income">
        <div class="card-label">Total Income</div>
        <div class="card-value">$${fmt(data.total_income)}</div>
        ${prevIncome > 0 ? `<div class="card-change ${incomeChange >= 0 ? "positive" : "negative"}">${incomeChange >= 0 ? "+" : ""}${incomeChange.toFixed(1)}% vs ${parseInt(data.year) - 1}</div>` : ""}
      </div>
      <div class="card expense">
        <div class="card-label">Total Expenses</div>
        <div class="card-value">$${fmt(data.total_expenses)}</div>
        ${prevExpenses > 0 ? `<div class="card-change ${expenseChange <= 0 ? "positive" : "negative"}">${expenseChange >= 0 ? "+" : ""}${expenseChange.toFixed(1)}% vs ${parseInt(data.year) - 1}</div>` : ""}
      </div>
      <div class="card ${data.net >= 0 ? "income" : "expense"}">
        <div class="card-label">Net Savings</div>
        <div class="card-value">${data.net < 0 ? "-" : ""}$${fmt(data.net)}</div>
      </div>
      <div class="card">
        <div class="card-label">Savings Rate</div>
        <div class="card-value ${data.savings_rate >= 0 ? "positive" : "negative"}">${data.savings_rate.toFixed(1)}%</div>
      </div>
      <div class="card">
        <div class="card-label">Avg Monthly Spend</div>
        <div class="card-value">$${fmt(data.avg_monthly_expense)}</div>
      </div>
    </div>

    <div class="tax-section">
      <h3>Spending by Category</h3>
      <table class="tax-table">
        <thead><tr><th>Category</th><th>Transactions</th><th>Total</th><th>%</th><th>Monthly Avg</th></tr></thead>
        <tbody>
          ${expenseCategories.sort((a, b) => a.total - b.total).map(c => {
            const pct = totalCatExpenses > 0 ? (Math.abs(c.total) / totalCatExpenses * 100) : 0;
            const monthlyAvg = Math.abs(c.total) / Math.max(data.monthly.length, 1);
            return `
            <tr>
              <td>${escapeHtml(c.category || "Uncategorized")}</td>
              <td>${c.count}</td>
              <td class="negative">$${fmt(Math.abs(c.total))}</td>
              <td>${pct.toFixed(1)}%</td>
              <td>$${fmt(monthlyAvg)}</td>
            </tr>`;
          }).join("")}
          <tr class="tax-total">
            <td>Total</td><td></td>
            <td class="negative">$${fmt(totalCatExpenses)}</td>
            <td></td><td>$${fmt(totalCatExpenses / Math.max(data.monthly.length, 1))}</td>
          </tr>
        </tbody>
      </table>
    </div>

    ${data.business.length > 0 ? `
    <div class="tax-section">
      <h3>Business Expenses</h3>
      <table class="tax-table">
        <thead><tr><th>Category</th><th>Count</th><th>Total</th></tr></thead>
        <tbody>
          ${data.business.map(b => `
            <tr>
              <td>${escapeHtml(b.category)}</td>
              <td>${b.count}</td>
              <td class="negative">$${fmt(Math.abs(b.total))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>` : ""}

    <div class="tax-section">
      <h3>Top Merchants</h3>
      <table class="tax-table">
        <thead><tr><th>Merchant</th><th>Visits</th><th>Total Spent</th></tr></thead>
        <tbody>
          ${data.top_merchants.map(m => `
            <tr>
              <td>${escapeHtml(m.description.substring(0, 50))}</td>
              <td>${m.count}</td>
              <td class="negative">$${fmt(Math.abs(m.total))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>

    <div class="charts-grid">
      <div class="tax-section">
        <h3>Biggest Expenses</h3>
        <table class="tax-table">
          <thead><tr><th>Date</th><th>Description</th><th>Amount</th></tr></thead>
          <tbody>
            ${data.biggest_expenses.map(t => `
              <tr>
                <td>${t.date}</td>
                <td>${escapeHtml(t.description.substring(0, 45))}</td>
                <td class="negative">$${fmt(Math.abs(t.amount))}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
      <div class="tax-section">
        <h3>Biggest Income</h3>
        <table class="tax-table">
          <thead><tr><th>Date</th><th>Description</th><th>Amount</th></tr></thead>
          <tbody>
            ${data.biggest_income.map(t => `
              <tr>
                <td>${t.date}</td>
                <td>${escapeHtml(t.description.substring(0, 45))}</td>
                <td class="positive">$${fmt(t.amount)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>

    <div class="tax-section">
      <h3>Data Sources</h3>
      <table class="tax-table">
        <thead><tr><th>Source</th><th>Transactions</th></tr></thead>
        <tbody>
          ${data.sources.map(s => `
            <tr><td>${escapeHtml(s.source_type)}</td><td>${s.count}</td></tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

// --- Shared Expenses Tab ---

type SharedDim = "none" | "category" | "tag";

let lastSharedData: SharedExpensesResponse | null = null;

async function loadSharedExpenses() {
  const data = await api.sharedExpenses();
  lastSharedData = data;
  populateSharedFilter(data);
  rerenderShared();
}

// Re-render derived views from cached data + current controls (no refetch).
// Filter (category/tag) scopes everything; Hide settled only declutters the table.
function rerenderShared() {
  if (!lastSharedData) return;
  const scoped = sharedFilteredItems();
  renderSharedSummary(scoped);
  renderSharedBreakdown(scoped);
  const hideSettled = (document.getElementById("shared-hide-settled") as HTMLInputElement)?.checked;
  renderSharedTable(hideSettled ? scoped.filter((i) => !i.is_settled) : scoped);
}

function sharedGroupBy(): SharedDim {
  return ((document.getElementById("shared-group-by") as HTMLSelectElement)?.value || "none") as SharedDim;
}

function sharedKeys(item: SharedExpenseItem, dim: SharedDim): string[] {
  if (dim === "category") return [item.category_name || "Uncategorized"];
  if (dim === "tag") return item.tags.length ? item.tags : ["(untagged)"];
  return [];
}

// Items after the active Category/Tag filter (independent of grouping).
// "Exclude" inverts the match, so you can drop a tag/category (e.g. the NZ trip).
function sharedFilteredItems(): SharedExpenseItem[] {
  const data = lastSharedData!;
  const filterVal = (document.getElementById("shared-filter") as HTMLSelectElement)?.value || "";
  if (!filterVal) return data.items;
  const exclude = (document.getElementById("shared-filter-exclude") as HTMLInputElement)?.checked;
  const sep = filterVal.indexOf(":");
  const dim = filterVal.slice(0, sep) as SharedDim;
  const val = filterVal.slice(sep + 1);
  return data.items.filter((i) => sharedKeys(i, dim).includes(val) !== exclude);
}

// One Filter dropdown spanning both dimensions, grouped with <optgroup>.
function populateSharedFilter(data: SharedExpensesResponse) {
  const sel = document.getElementById("shared-filter") as HTMLSelectElement | null;
  if (!sel) return;
  const prev = sel.value;
  const opt = (dim: string, g: { key: string }) =>
    `<option value="${escapeHtml(dim + ":" + g.key)}">${escapeHtml(g.key)}</option>`;
  sel.innerHTML =
    `<option value="">All</option>` +
    `<optgroup label="Category">${data.by_category.map((g) => opt("category", g)).join("")}</optgroup>` +
    `<optgroup label="Tag">${data.by_tag.map((g) => opt("tag", g)).join("")}</optgroup>`;
  sel.value = prev;
  if (sel.value !== prev) sel.value = "";
}

function computeSharedBreakdown(items: SharedExpenseItem[], dim: SharedDim) {
  const m = new Map<string, { shared: number; settled: number }>();
  for (const it of items) {
    for (const k of sharedKeys(it, dim)) {
      const g = m.get(k) || { shared: 0, settled: 0 };
      g.shared += it.share_amount;
      if (it.is_settled) g.settled += it.share_amount;
      m.set(k, g);
    }
  }
  return [...m.entries()]
    .map(([key, g]) => ({ key, shared: g.shared, settled: g.settled, owing: g.shared - g.settled }))
    .sort((a, b) => b.owing - a.owing);
}

// Pivot of balance owing by the grouped dimension; recomputes from filtered items.
function renderSharedBreakdown(items: SharedExpenseItem[]) {
  const el = document.getElementById("shared-breakdown")!;
  const dim = sharedGroupBy();
  if (dim === "none") { el.innerHTML = ""; return; }
  const rows = computeSharedBreakdown(items, dim);
  // Total is over unique items, not a sum of rows — a tag pivot would otherwise
  // double-count any item carrying more than one tag.
  const totShared = items.reduce((s, i) => s + i.share_amount, 0);
  const totSettled = items.reduce((s, i) => s + (i.is_settled ? i.share_amount : 0), 0);
  const tot = { shared: totShared, settled: totSettled, owing: totShared - totSettled };
  el.innerHTML = `
    <div class="pivot-card">
      <div class="pivot-title">Balance owing by ${dim}</div>
      <table class="breakdown-table">
        <thead><tr>
          <th>${dim === "category" ? "Category" : "Tag"}</th>
          <th class="num">Owed</th><th class="num">Settled</th><th class="num">Balance owing</th>
        </tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td>${escapeHtml(r.key)}</td>
            <td class="num">$${fmt(r.shared)}</td>
            <td class="num muted">$${fmt(r.settled)}</td>
            <td class="num ${r.owing > 0 ? "negative" : "muted"}">$${fmt(r.owing)}</td>
          </tr>`).join("")}
        </tbody>
        <tfoot><tr class="pivot-total">
          <td>Total</td>
          <td class="num">$${fmt(tot.shared)}</td>
          <td class="num muted">$${fmt(tot.settled)}</td>
          <td class="num ${tot.owing > 0 ? "negative" : ""}">$${fmt(tot.owing)}</td>
        </tr></tfoot>
      </table>
    </div>`;
}

function renderSharedSummary(items: SharedExpenseItem[]) {
  const el = document.getElementById("shared-summary")!;
  const totalShared = items.reduce((s, i) => s + i.share_amount, 0);
  const totalSettled = items.reduce((s, i) => s + (i.is_settled ? i.share_amount : 0), 0);
  const balance = totalShared - totalSettled;
  el.innerHTML = `
    <div class="summary-cards">
      <div class="card expense">
        <div class="card-label">Total Owed</div>
        <div class="card-value">$${fmt(totalShared)}</div>
      </div>
      <div class="card income">
        <div class="card-label">Total Settled</div>
        <div class="card-value">$${fmt(totalSettled)}</div>
      </div>
      <div class="card ${balance > 0 ? "expense" : "income"}">
        <div class="card-label">Balance Owing</div>
        <div class="card-value">$${fmt(balance)}</div>
      </div>
    </div>
  `;
}

function sharedRowHtml(item: SharedExpenseItem): string {
  return `
    <tr class="${item.is_settled ? "settled-row" : ""}">
      <td>${item.date}</td>
      <td>${escapeHtml(item.description)}</td>
      <td class="negative">$${fmt(Math.abs(item.amount))}</td>
      <td>
        <input type="number" class="split-input" data-id="${item.id}"
          value="${item.split_pct}" min="0" max="100" step="5" />%
      </td>
      <td class="negative">$${fmt(item.share_amount)}</td>
      <td>${escapeHtml(item.category_name || "")}</td>
      <td>${item.tags.map((t) => `<span class="tag-chip">${escapeHtml(t)}</span>`).join(" ")}</td>
      <td>
        <input type="checkbox" class="settled-check" data-id="${item.id}"
          ${item.is_settled ? "checked" : ""} />
      </td>
      <td>
        <button class="remove-shared-btn" data-id="${item.id}" title="Remove from shared">&times;</button>
      </td>
    </tr>`;
}

function renderSharedTable(items: SharedExpenseItem[]) {
  const groupBy = sharedGroupBy();
  const tbody = document.getElementById("shared-body")!;

  const owingOf = (rows: SharedExpenseItem[]) =>
    rows.reduce((s, i) => s + (i.is_settled ? 0 : i.share_amount), 0);

  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty-row">No shared expenses match the current filter.</td></tr>`;
  } else if (groupBy === "none") {
    tbody.innerHTML = items.map(sharedRowHtml).join("");
  } else {
    const groups = new Map<string, SharedExpenseItem[]>();
    for (const item of items) {
      for (const k of sharedKeys(item, groupBy)) {
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k)!.push(item);
      }
    }
    const order = [...groups.keys()].sort((a, b) => owingOf(groups.get(b)!) - owingOf(groups.get(a)!));
    tbody.innerHTML = order.map((k) => {
      const rows = groups.get(k)!;
      const full = rows.reduce((s, i) => s + i.share_amount, 0);
      return `<tr class="group-header"><td colspan="9">${escapeHtml(k)} <span class="muted">— balance owing</span> ` +
        `$${fmt(owingOf(rows))} <span class="muted">of $${fmt(full)}</span></td></tr>` +
        rows.map(sharedRowHtml).join("");
    }).join("");
  }

  tbody.querySelectorAll<HTMLInputElement>(".settled-check").forEach((cb) => {
    cb.addEventListener("change", async () => {
      const id = Number(cb.dataset.id);
      await api.updateSharedExpense(id, { is_settled: cb.checked });
      await loadSharedExpenses();
    });
  });

  tbody.querySelectorAll<HTMLInputElement>(".split-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = Number(input.dataset.id);
      const pct = parseFloat(input.value);
      if (pct >= 0 && pct <= 100) {
        await api.updateSharedExpense(id, { split_pct: pct });
        await loadSharedExpenses();
      }
    });
  });

  tbody.querySelectorAll<HTMLButtonElement>(".remove-shared-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.dataset.id);
      await api.deleteSharedExpense(id);
      await loadSharedExpenses();
    });
  });
}

// --- Economics Tab ---

async function loadEconomics() {
  const year = (document.getElementById("econ-year") as HTMLSelectElement)?.value ||
    String(new Date().getFullYear());
  const data = await api.economicsSummary(year);
  renderEconomics(data);
}

function renderEconomics(data: EconomicsSummary) {
  const el = document.getElementById("econ-content")!;
  const sp = data.spending_power;
  const tax = data.tax_analysis;
  const nw = data.net_worth;
  const cpi = data.cpi;

  const hasCpi = cpi.current_index != null;
  const noCpiMsg = `<p class="econ-notice">No CPI data loaded. Click "Sync CPI Data" to fetch from ABS.</p>`;

  el.innerHTML = `
    ${!hasCpi ? noCpiMsg : ""}

    <h2>${data.year} Economics</h2>

    <!-- Summary cards -->
    <div class="summary-cards econ-cards">
      <div class="card income">
        <div class="card-label">Gross Salary</div>
        <div class="card-value">$${fmt(sp.salary)}</div>
        ${sp.salary_real != null ? `<div class="card-sub">Real (${cpi.base_year}$): $${fmt(sp.salary_real)}</div>` : ""}
      </div>
      <div class="card expense">
        <div class="card-label">Total Tax</div>
        <div class="card-value">$${fmt(tax.total_tax)}</div>
        <div class="card-sub">${tax.effective_rate}% effective rate</div>
      </div>
      <div class="card">
        <div class="card-label">After-Tax Income</div>
        <div class="card-value">$${fmt(tax.after_tax)}</div>
        ${tax.after_tax_real != null ? `<div class="card-sub">Real: $${fmt(tax.after_tax_real)}</div>` : ""}
      </div>
      <div class="card ${(sp.purchasing_power_loss ?? 0) > 0 ? 'expense' : 'income'}">
        <div class="card-label">Purchasing Power Lost</div>
        <div class="card-value">${sp.purchasing_power_loss != null ? sp.purchasing_power_loss.toFixed(1) + "%" : "N/A"}</div>
        <div class="card-sub">Since ${cpi.base_year}</div>
      </div>
      <div class="card">
        <div class="card-label">CPI (YoY)</div>
        <div class="card-value">${cpi.yoy_change != null ? cpi.yoy_change.toFixed(1) + "%" : "N/A"}</div>
        <div class="card-sub">Index: ${cpi.current_index ?? "N/A"}</div>
      </div>
      <div class="card ${(sp.real_savings_rate ?? 0) >= 0 ? 'income' : 'expense'}">
        <div class="card-label">Real Savings Rate</div>
        <div class="card-value">${sp.real_savings_rate != null ? sp.real_savings_rate.toFixed(1) + "%" : "N/A"}</div>
        <div class="card-sub">After tax & expenses</div>
      </div>
    </div>

    <!-- Tax breakdown -->
    <div class="tax-section">
      <h3>Where Your Tax Dollars Go</h3>
      <div class="econ-split">
        <div class="econ-chart-col">
          <canvas id="chart-tax-breakdown"></canvas>
        </div>
        <div class="econ-table-col">
          <table class="tax-table">
            <thead><tr><th>Category</th><th>Amount</th><th>%</th></tr></thead>
            <tbody>
              <tr><td><strong>PAYG Income Tax</strong></td><td>$${fmt(tax.payg)}</td><td></td></tr>
              <tr><td><strong>Medicare Levy</strong></td><td>$${fmt(tax.medicare)}</td><td></td></tr>
              <tr class="tax-total"><td><strong>Total Tax</strong></td><td><strong>$${fmt(tax.total_tax)}</strong></td><td></td></tr>
              <tr><td colspan="3" style="padding-top:0.5rem;"><em>Your $${fmt(tax.total_tax)} funds:</em></td></tr>
              ${tax.tax_breakdown.map(b => `
                <tr>
                  <td>${escapeHtml(b.category)}</td>
                  <td>$${fmt(b.amount)}</td>
                  <td>${b.pct}%</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Income waterfall -->
    <div class="tax-section">
      <h3>Income Waterfall</h3>
      <div class="waterfall">
        ${renderWaterfall(sp.salary, tax)}
      </div>
    </div>

    <!-- Inflation-adjusted spending -->
    ${data.inflation_adjusted_spending.length > 0 ? `
    <div class="tax-section">
      <h3>Inflation-Adjusted Spending</h3>
      <p class="tax-hint">All "real" values in ${cpi.base_year} dollars</p>
      <table class="tax-table">
        <thead>
          <tr>
            <th>Category</th>
            <th>Nominal</th>
            ${hasCpi ? "<th>Real</th><th>Real YoY</th>" : ""}
          </tr>
        </thead>
        <tbody>
          ${data.inflation_adjusted_spending.map(s => `
            <tr>
              <td>${escapeHtml(s.category)}</td>
              <td class="negative">$${fmt(s.nominal)}</td>
              ${hasCpi ? `
                <td>$${s.real != null ? fmt(s.real) : "N/A"}</td>
                <td class="${(s.real_change_pct ?? 0) > 0 ? 'negative' : (s.real_change_pct ?? 0) < 0 ? 'positive' : ''}">${s.real_change_pct != null ? (s.real_change_pct > 0 ? "+" : "") + s.real_change_pct.toFixed(1) + "%" : "-"}</td>
              ` : ""}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>` : ""}

    <!-- Net worth -->
    <div class="tax-section">
      <h3>Net Worth — Real vs Nominal</h3>
      <div class="summary-cards" style="margin-bottom: 1rem;">
        <div class="card">
          <div class="card-label">Nominal</div>
          <div class="card-value ${nw.nominal >= 0 ? "positive" : "negative"}">${nw.nominal < 0 ? "-" : ""}$${fmt(nw.nominal)}</div>
        </div>
        ${nw.real != null ? `
        <div class="card">
          <div class="card-label">Real (${cpi.base_year}$)</div>
          <div class="card-value ${nw.real >= 0 ? "positive" : "negative"}">${nw.real < 0 ? "-" : ""}$${fmt(nw.real)}</div>
        </div>
        <div class="card ${(nw.real_change_pct ?? 0) >= 0 ? 'income' : 'expense'}">
          <div class="card-label">Real Change YoY</div>
          <div class="card-value">${nw.real_change_pct != null ? (nw.real_change_pct > 0 ? "+" : "") + nw.real_change_pct.toFixed(1) + "%" : "N/A"}</div>
        </div>
        ` : ""}
      </div>
    </div>

    <!-- CPI history chart -->
    ${data.cpi_history.length > 0 ? `
    <div class="tax-section">
      <h3>CPI Trend (All Groups, Australia)</h3>
      <div class="chart-card">
        <canvas id="chart-cpi-history"></canvas>
      </div>
    </div>` : ""}
  `;

  // Render charts
  if (tax.tax_breakdown.length > 0) {
    renderTaxBreakdownChart(
      document.getElementById("chart-tax-breakdown") as HTMLCanvasElement,
      tax.tax_breakdown,
    );
  }
  if (data.cpi_history.length > 0) {
    renderCpiHistoryChart(
      document.getElementById("chart-cpi-history") as HTMLCanvasElement,
      data.cpi_history,
    );
  }
}

function renderWaterfall(salary: number, tax: EconomicsSummary["tax_analysis"]): string {
  if (salary <= 0) return "<p>No salary data for this year.</p>";
  const steps = [
    { label: "Gross Salary", value: salary, color: "var(--green)" },
    { label: "PAYG Tax", value: -tax.payg, color: "var(--red)" },
    { label: "Medicare", value: -tax.medicare, color: "var(--red)" },
    { label: "After Tax", value: tax.after_tax, color: "var(--accent)" },
  ];
  const maxVal = salary;
  return steps.map(s => {
    const width = Math.abs(s.value) / maxVal * 100;
    return `
      <div class="waterfall-row">
        <span class="waterfall-label">${s.label}</span>
        <div class="waterfall-bar-track">
          <div class="waterfall-bar" style="width: ${width}%; background: ${s.color};"></div>
        </div>
        <span class="waterfall-value ${s.value < 0 ? 'negative' : 'positive'}">${s.value < 0 ? "-" : ""}$${fmt(Math.abs(s.value))}</span>
      </div>
    `;
  }).join("");
}

// --- Helpers ---

function fmt(val: number): string {
  return Math.abs(val).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// --- Recurring Schedules Tab ---

async function loadRecurring() {
  const data = await api.recurringSchedules();
  renderRecurring(data);
}

function renderRecurring(data: import("./api").SchedulesResponse) {
  const summary = document.getElementById("recurring-summary")!;
  summary.innerHTML = `
    <div class="summary-cards">
      <div class="card expense">
        <div class="card-label">Expected to date</div>
        <div class="card-value">$${fmt(data.total_expected)}</div>
      </div>
      <div class="card income">
        <div class="card-label">Paid</div>
        <div class="card-value">$${fmt(data.total_paid)}</div>
      </div>
      <div class="card">
        <div class="card-label">Balance owing</div>
        <div class="card-value ${data.total_owing > 0 ? "negative" : ""}">$${fmt(data.total_owing)}</div>
      </div>
    </div>`;

  const el = document.getElementById("recurring-content")!;
  if (!data.schedules.length) {
    el.innerHTML = `<p class="muted">No recurring schedules configured. Add them to <code>config/schedules.yaml</code>.</p>`;
    return;
  }

  el.innerHTML = data.schedules.map((s) => {
    const owingClass = s.balance_owing > 0 ? "negative" : "muted";
    const settleNote = s.settle_enabled
      ? `${s.payments.length} payment${s.payments.length === 1 ? "" : "s"} matched`
      : "manual tracking (no payment matching configured)";
    return `
      <div class="pivot-card">
        <div class="pivot-title">${escapeHtml(s.name)}</div>
        <table class="mini-table">
          <tbody>
            <tr><td>Counterparty</td><td class="num">${escapeHtml(s.counterparty || "—")}</td></tr>
            <tr><td>${escapeHtml(s.frequency)} amount</td><td class="num">$${fmt(s.amount)}</td></tr>
            <tr><td>Their share (${s.their_pct}%)</td><td class="num">$${fmt(s.their_share)}</td></tr>
            <tr><td>Occurrences due (since ${s.start})</td><td class="num">${s.num_due}</td></tr>
            <tr><td>Expected to date</td><td class="num">$${fmt(s.expected_to_date)}</td></tr>
            <tr><td>Paid</td><td class="num">$${fmt(s.paid)}</td></tr>
            <tr><td><strong>Balance owing</strong></td><td class="num ${owingClass}"><strong>$${fmt(s.balance_owing)}</strong></td></tr>
            <tr><td>Next due</td><td class="num">${s.next_due}</td></tr>
          </tbody>
        </table>
        <div class="muted" style="margin:0.25rem 0 0.5rem;">${escapeHtml(settleNote)}${s.notes ? " · " + escapeHtml(s.notes) : ""}</div>
        <table class="breakdown-table">
          <thead><tr><th>Due date</th><th class="num">Full amount</th><th class="num">Their share</th></tr></thead>
          <tbody>
            ${s.occurrences.map((o) => `<tr>
              <td>${o.date}</td>
              <td class="num muted">$${fmt(o.amount)}</td>
              <td class="num">$${fmt(o.their_share)}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  }).join("");
}

async function loadTransactions() {
  const filters = getTransactionFilters();
  const transactions = await api.transactions(filters);
  renderTransactionTable(transactions);
}

function renderTransactionTable(transactions: Transaction[]) {
  const tbody = document.getElementById("txn-body")!;
  tbody.innerHTML = transactions
    .map((t) => {
      const amtClass = t.amount >= 0 ? "positive" : "negative";
      // Show original currency if amount wasn't converted to AUD
      const isUnconvertedFx = t.original_currency && t.original_amount &&
        Math.abs(t.amount) === Math.abs(t.original_amount);
      const fxInfo =
        t.original_amount && t.original_currency && !isUnconvertedFx
          ? ` <span class="fx">(${t.original_currency} ${t.original_amount.toFixed(2)})</span>`
          : "";
      const amtPrefix = isUnconvertedFx ? `${t.original_currency} ` : "$";
      const amtSuffix = isUnconvertedFx ? ` <span class="fx">(approx AUD)</span>` : "";
      return `
      <tr data-id="${t.id}">
        <td>${t.date}</td>
        <td>${escapeHtml(t.description)}${fxInfo}</td>
        <td class="${amtClass}">${amtPrefix}${Math.abs(t.amount).toFixed(2)}${amtSuffix}</td>
        <td>
          <select class="cat-select" data-id="${t.id}">
            ${allCategories.map((c) =>
              `<option value="${escapeHtml(c.name)}" ${c.name === t.category_name ? "selected" : ""}>${escapeHtml(c.name)}</option>`
            ).join("")}
          </select>
        </td>
        <td>${escapeHtml(t.account_name || "")}</td>
        <td>
          <input type="text" class="notes-input" data-id="${t.id}"
            value="${escapeHtml(t.notes || "")}" placeholder="Add note..." />
        </td>
        <td>
          ${t.amount < 0 ? `<button class="share-btn" data-id="${t.id}" title="Add to shared expenses">Split</button>` : ""}
        </td>
      </tr>`;
    })
    .join("");

  tbody.querySelectorAll<HTMLSelectElement>(".cat-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const id = Number(sel.dataset.id);
      await api.updateTransaction(id, { category_name: sel.value });
      sel.classList.add("saved");
      setTimeout(() => sel.classList.remove("saved"), 1000);
    });
  });

  tbody.querySelectorAll<HTMLInputElement>(".notes-input").forEach((input) => {
    input.addEventListener("change", async () => {
      const id = Number(input.dataset.id);
      await api.updateTransaction(id, { notes: input.value });
      input.classList.add("saved");
      setTimeout(() => input.classList.remove("saved"), 1000);
    });
  });

  tbody.querySelectorAll<HTMLButtonElement>(".share-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = Number(btn.dataset.id);
      try {
        await api.addSharedExpense(id);
        btn.textContent = "Shared";
        btn.disabled = true;
        btn.classList.add("saved");
      } catch {
        btn.textContent = "Error";
      }
    });
  });
}

async function loadBudget() {
  const month = (document.getElementById("budget-month") as HTMLInputElement)?.value;
  const data = await api.budgetVsActual(month);
  const container = document.getElementById("budget-bars")!;

  container.innerHTML = data
    .map((b) => {
      const pct = b.budget > 0 ? Math.min((b.actual / b.budget) * 100, 100) : 0;
      const over = b.remaining < 0;
      return `
      <div class="budget-row ${over ? "over" : ""}">
        <div class="budget-label">
          <span>${escapeHtml(b.category)}</span>
          <span>$${b.actual.toFixed(0)} / $${b.budget.toFixed(0)}</span>
        </div>
        <div class="budget-track">
          <div class="budget-fill ${over ? "over" : ""}" style="width: ${pct}%"></div>
        </div>
        ${over ? `<div class="budget-warning">Over by $${Math.abs(b.remaining).toFixed(0)}</div>` : ""}
      </div>`;
    })
    .join("");
}

async function loadTrends() {
  const from = (document.getElementById("trends-from") as HTMLInputElement)?.value;
  const to = (document.getElementById("trends-to") as HTMLInputElement)?.value;
  const data = await api.trends(from, to);
  renderTrendsChart(
    document.getElementById("chart-trends") as HTMLCanvasElement,
    data
  );
}

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  // innerHTML escapes &<> but not quotes; escape them too so values are safe
  // inside double/single-quoted attributes (data-copy, title).
  return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- Filter event listeners ---

function debounce(fn: () => void, ms: number) {
  let timer: number;
  return () => {
    clearTimeout(timer);
    timer = window.setTimeout(fn, ms);
  };
}

document.getElementById("txn-from")?.addEventListener("change", loadTransactions);
document.getElementById("txn-to")?.addEventListener("change", loadTransactions);
document.getElementById("txn-category")?.addEventListener("change", loadTransactions);
document.getElementById("txn-account")?.addEventListener("change", loadTransactions);
document.getElementById("txn-search")?.addEventListener("input", debounce(loadTransactions, 300));
document.getElementById("dash-year")?.addEventListener("change", loadDashboard);
document.getElementById("dash-exclude-loans")?.addEventListener("change", loadDashboard);
document.getElementById("dash-exclude-transfers")?.addEventListener("change", loadDashboard);
document.getElementById("budget-month")?.addEventListener("change", loadBudget);
document.getElementById("trends-from")?.addEventListener("change", loadTrends);
document.getElementById("trends-to")?.addEventListener("change", loadTrends);
document.getElementById("review-year")?.addEventListener("change", loadYearReview);
document.getElementById("ss-fy")?.addEventListener("change", loadSpreadsheet);
document.getElementById("tax-fy")?.addEventListener("change", loadTax);
document.getElementById("shared-hide-settled")?.addEventListener("change", rerenderShared);
document.getElementById("shared-group-by")?.addEventListener("change", rerenderShared);
document.getElementById("shared-filter")?.addEventListener("change", rerenderShared);
document.getElementById("shared-filter-exclude")?.addEventListener("change", rerenderShared);
document.getElementById("econ-year")?.addEventListener("change", loadEconomics);
document.getElementById("econ-sync-cpi")?.addEventListener("click", async () => {
  const btn = document.getElementById("econ-sync-cpi") as HTMLButtonElement;
  btn.textContent = "Syncing...";
  btn.disabled = true;
  try {
    const result = await api.syncCpi();
    btn.textContent = `Synced (${result.rows_synced} rows)`;
    await loadEconomics();
  } catch {
    btn.textContent = "Sync Failed";
  } finally {
    setTimeout(() => {
      btn.textContent = "Sync CPI Data";
      btn.disabled = false;
    }, 3000);
  }
});

// --- Init ---

async function init() {
  allCategories = await api.categories();
  await populateFilters();
  populateFYSelect();
  populateSSFYSelect();
  populateReviewYearSelect();
  populateEconYearSelect();
  initSpreadsheet();
  // Open the tab named in the URL hash (so a refresh stays put); default dashboard.
  activateTab(location.hash.slice(1) || "dashboard");
}

function populateSSFYSelect() {
  const sel = document.getElementById("ss-fy") as HTMLSelectElement | null;
  if (!sel) return;
  const now = new Date();
  const currentFY = now.getMonth() >= 6 ? now.getFullYear() + 1 : now.getFullYear();
  for (let fy = currentFY; fy >= currentFY - 7; fy--) {
    const opt = document.createElement("option");
    opt.value = String(fy);
    opt.textContent = `FY ${fy - 1}-${String(fy).slice(2)}`;
    sel.appendChild(opt);
  }
  sel.value = String(currentFY - 1);
}

function populateFYSelect() {
  const sel = document.getElementById("tax-fy") as HTMLSelectElement | null;
  if (!sel) return;
  const now = new Date();
  const currentFY = now.getMonth() >= 6 ? now.getFullYear() + 1 : now.getFullYear();
  for (let fy = currentFY; fy >= currentFY - 7; fy--) {
    const opt = document.createElement("option");
    opt.value = String(fy);
    opt.textContent = `FY ${fy - 1}-${String(fy).slice(2)}`;
    sel.appendChild(opt);
  }
  // Default to most recent complete FY
  sel.value = String(currentFY - 1);
}

function populateEconYearSelect() {
  const sel = document.getElementById("econ-year") as HTMLSelectElement | null;
  if (!sel) return;
  const currentYear = new Date().getFullYear();
  for (let y = currentYear; y >= currentYear - 10; y--) {
    const opt = document.createElement("option");
    opt.value = String(y);
    opt.textContent = String(y);
    sel.appendChild(opt);
  }
  sel.value = String(currentYear);
}

function populateReviewYearSelect() {
  const sel = document.getElementById("review-year") as HTMLSelectElement | null;
  if (!sel) return;
  const currentYear = new Date().getFullYear();
  for (let y = currentYear; y >= currentYear - 10; y--) {
    const opt = document.createElement("option");
    opt.value = String(y);
    opt.textContent = String(y);
    sel.appendChild(opt);
  }
  sel.value = "2025";
}

init();
