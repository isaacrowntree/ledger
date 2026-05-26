import { api, type Transaction, type Category, type AccountSummary, type Holding, type TaxSummary, type ATOReturn, type SharedExpenseItem, type SharedExpensesResponse, type EconomicsSummary } from "./api";
import { renderMonthlyChart, renderCategoryChart, renderTrendsChart, renderTaxBreakdownChart, renderCpiHistoryChart } from "./charts";
import { populateFilters, getTransactionFilters } from "./filters";
import { initSpreadsheet, loadSpreadsheet } from "./spreadsheet";
import "./style.css";

let allCategories: Category[] = [];

// --- Tab navigation ---

document.querySelectorAll<HTMLButtonElement>(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    const viewId = btn.dataset.tab!;
    document.getElementById(viewId)!.classList.add("active");
    loadView(viewId);
  });
});

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
    case "tax":
      return loadTax();
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

  el.innerHTML = `
    <div class="tax-header">
      <h2>${escapeHtml(data.fy_label)}</h2>
      <span class="tax-dates">Australian Individual Tax Return</span>
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
        <p class="tax-hint">${escapeHtml(r.address)} | ${r.ownership_pct}% ownership | ${r.rental_weeks} weeks rented</p>
        <table class="tax-table">
          <thead><tr><th>Line Item</th><th>Amount</th></tr></thead>
          <tbody>
            <tr><td>Gross rental income</td><td class="positive">$${fmt(r.gross_income)}</td></tr>
            <tr><td>Your share (${r.ownership_pct}%)</td><td class="positive">$${fmt(r.income_share)}</td></tr>
            ${r.expenses.map((e) => `
              <tr><td>${escapeHtml(e.ato_label)}</td><td class="negative">-$${fmt(e.share)}</td></tr>
            `).join("")}
            ${r.depreciation > 0 ? `<tr><td>Capital allowances</td><td class="negative">-$${fmt(r.depreciation)}</td></tr>` : ""}
            <tr class="tax-total">
              <td>Net rent</td>
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

async function loadSharedExpenses() {
  const data = await api.sharedExpenses();
  renderSharedSummary(data);
  renderSharedTable(data.items);
}

function renderSharedSummary(data: SharedExpensesResponse) {
  const el = document.getElementById("shared-summary")!;
  el.innerHTML = `
    <div class="summary-cards">
      <div class="card expense">
        <div class="card-label">Total Owed</div>
        <div class="card-value">$${fmt(data.total_shared)}</div>
      </div>
      <div class="card income">
        <div class="card-label">Total Settled</div>
        <div class="card-value">$${fmt(data.total_settled)}</div>
      </div>
      <div class="card ${data.balance_owing > 0 ? "expense" : "income"}">
        <div class="card-label">Balance Owing</div>
        <div class="card-value">$${fmt(data.balance_owing)}</div>
      </div>
    </div>
  `;
}

function renderSharedTable(items: SharedExpenseItem[]) {
  const hideSettled = (document.getElementById("shared-hide-settled") as HTMLInputElement)?.checked;
  const filtered = hideSettled ? items.filter((i) => !i.is_settled) : items;
  const tbody = document.getElementById("shared-body")!;

  tbody.innerHTML = filtered
    .map((item) => `
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
        <td>
          <input type="checkbox" class="settled-check" data-id="${item.id}"
            ${item.is_settled ? "checked" : ""} />
        </td>
        <td>
          <button class="remove-shared-btn" data-id="${item.id}" title="Remove from shared">&times;</button>
        </td>
      </tr>
    `)
    .join("");

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
  div.textContent = s;
  return div.innerHTML;
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
document.getElementById("shared-hide-settled")?.addEventListener("change", loadSharedExpenses);
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
  await loadDashboard();
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
