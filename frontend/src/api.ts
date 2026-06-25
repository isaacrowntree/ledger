const BASE = "/api";

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v);
    });
  }
  const res = await fetch(url.toString());
  return res.json();
}

async function patch<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export interface Transaction {
  id: number;
  date: string;
  description: string;
  amount: number;
  original_amount: number | null;
  original_currency: string | null;
  fee: number;
  category_id: number | null;
  category_name: string | null;
  category_confidence: number | null;
  account_name: string;
  account_type: string;
  is_transfer: number;
  notes: string | null;
  source_type: string;
}

export interface Category {
  id: number;
  name: string;
  is_income: number;
  budget_monthly: number | null;
}

export interface Account {
  id: number;
  name: string;
  source_type: string;
  currency: string;
  account_type: string;
}

export interface AccountSummary {
  id: number;
  name: string;
  source_type: string;
  currency: string;
  account_type: string;
  balance: number;
  transaction_count: number;
}

export interface Holding {
  id: number;
  asset_type: string;
  name: string;
  ticker: string | null;
  units: number;
  cost_basis: number;
  current_value: number;
  as_at_date: string | null;
}

export interface AccountsSummaryResponse {
  accounts: AccountSummary[];
  holdings: Holding[];
}

export interface MonthlySummary {
  month: string;
  expenses: number;
  income: number;
}

export interface CategorySummary {
  category: string;
  is_income: number;
  total: number;
  count: number;
}

export interface BudgetItem {
  category: string;
  budget: number;
  actual: number;
  remaining: number;
  is_income: boolean;
}

export interface TrendPoint {
  month: string;
  category: string;
  total: number;
}

export interface TaxSummary {
  fy: string;
  fy_label: string;
  fy_start: string;
  fy_end: string;
  categories: { category: string; is_income: number; total: number; count: number }[];
  business_transactions: { date: string; description: string; amount: number; category: string; account_name: string }[];
}

export interface TopMerchant {
  description: string;
  count: number;
  total: number;
}

export interface YearReview {
  year: string;
  total_income: number;
  total_expenses: number;
  net: number;
  savings_rate: number;
  avg_monthly_expense: number;
  monthly: { month: string; income: number; expenses: number }[];
  categories: { category: string; is_income: number; total: number; count: number }[];
  top_merchants: { description: string; count: number; total: number }[];
  business: { category: string; total: number; count: number }[];
  previous_year: { income: number; expenses: number };
  sources: { source_type: string; count: number }[];
  biggest_expenses: { date: string; description: string; amount: number; account_name: string }[];
  biggest_income: { date: string; description: string; amount: number; account_name: string }[];
}

export interface SpreadsheetOutgoing {
  id: number;
  date: string;
  description: string;
  amount: number;
  source_type: string;
  category_name: string | null;
  account_name: string;
  splits: { business_name: string; business_pct: number; business_amount: number }[];
  biz_pct: number;
  biz_amount: number;
}

export interface SpreadsheetIncoming {
  id: number;
  date: string;
  description: string;
  amount: number;
  source_type: string;
  category_name: string | null;
  account_name: string;
}

export interface RentalExpense {
  ato_label: string;
  raw_amount: number;
  share_amount: number;
}

export interface RentalProperty {
  name: string;
  address: string;
  ownership_pct: number;
  rental_weeks: number;
  gross_income: number;
  income_share: number;
  expenses: RentalExpense[];
  depreciation: { description: string; amount: number }[];
  total_expenses: number;
  net_rent: number;
}

export interface WorkTripsResponse {
  trips: {
    id: number;
    fy: number;
    name: string;
    start_date: string;
    end_date: string;
    expenses: { expense_type: string; amount: number; description: string }[];
    total: number;
  }[];
  wfh: {
    weeks: number;
    allocation_pct: number;
    rate_per_hour: number;
    hours: number;
    amount: number;
  };
}

export interface ATOLabelRow {
  code?: string;   // section rows use `code`...
  label?: string;  // ...carry-forward rows use `label`
  desc: string;
  value: number | string;
}

export interface ATOLodgedYear {
  fy: number;
  fy_label: string;
  receipt?: string;
  taxable_income: number;
  tax_withheld: number;
  carry_forward: ATOLabelRow[];
  sections: { name: string; rows: ATOLabelRow[] }[];
}

export interface ATOTaxpayer {
  id: string;
  name: string;
  reference: {
    tfn?: string;
    name?: string;
    date_of_birth?: string;
    abn?: string;
    business_name?: string;
    occupation?: string;
    health_insurer?: { id?: string; membership?: string };
    spouse?: { name?: string; date_of_birth?: string };
  };
  lodged: ATOLodgedYear[];
  latest_carry_forward: (ATOLabelRow & { from_fy: number; from_fy_label: string })[];
}

export interface ATOLodgedResponse {
  taxpayers: ATOTaxpayer[];
}

export interface DepreciationYear {
  fy: number;
  opening: number;
  decline: number;
  deductible: number;
  closing: number;
}

export interface DepreciationAsset {
  description: string;
  cost: number;
  acquired: string;
  method: string;
  effective_life: number;
  taxable_use_pct: number;
  years: DepreciationYear[];
}

export interface DepreciationRegister {
  owner: string;
  kind: string;
  ownership_pct: number;
  method_note?: string;
  assets: DepreciationAsset[];
  totals: Record<string, { decline: number; deductible: number; taxpayer_deductible: number; n_assets: number }>;
}

export interface DepreciationResponse {
  registers: DepreciationRegister[];
  fy_totals: Record<string, { decline: number; deductible: number; taxpayer_deductible: number }>;
}

export interface ATOReturn {
  fy: number;
  fy_label: string;
  income: {
    salary: number;
    interest: number;
    tax_withheld: number;
  };
  rental: {
    property: string;
    address: string;
    ownership_pct: number;
    floor_area_pct: number;
    rental_weeks: number;
    gross_income: number;
    income_share: number;
    expenses: {
      ato_label: string;
      raw: number;
      share: number;
      factor: number;
      apply: string[];
      n_txns: number;
    }[];
    depreciation: number;
    total_expenses: number;
    net_rent: number;
  }[];
  business: {
    name: string;
    abn: string;
    income: number;
    expenses: number;
    depreciation: number;
    net: number;
  }[];
  deductions: {
    wfh: { weeks: number; allocation_pct: number; amount: number };
    work_trips: {
      name: string;
      start_date: string;
      end_date: string;
      expenses: Record<string, number>;
      total: number;
    }[];
  };
  manual_entries: { label: string; amount: number; section: string; notes: string }[];
  spouse: { name: string; taxable_income: number };
  summary: {
    assessable_income: number;
    total_deductions: number;
    taxable_income: number;
    payg: number;
    medicare: number;
    total_tax: number;
    effective_rate: number;
    tax_withheld: number;
    refund_or_payable: number;  // positive = refund, negative = bill
  };
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  return res.json();
}

export interface SharedExpenseItem {
  id: number;
  transaction_id: number;
  split_pct: number;
  is_settled: number;
  settled_date: string | null;
  date: string;
  description: string;
  amount: number;
  notes: string | null;
  category_name: string | null;
  account_name: string | null;
  share_amount: number;
  tags: string[];
}

export interface SharedGroup {
  key: string;
  total_shared: number;
  total_settled: number;
  balance_owing: number;
}

export interface ScheduleOccurrence {
  date: string;
  amount: number;
  their_share: number;
}

export interface SchedulePayment {
  date: string;
  description: string;
  amount: number;
}

export interface RecurringSchedule {
  name: string;
  counterparty: string | null;
  frequency: string;
  amount: number;
  their_pct: number;
  their_share: number;
  start: string;
  notes: string;
  occurrences: ScheduleOccurrence[];
  num_due: number;
  expected_to_date: number;
  next_due: string;
  settle_enabled: boolean;
  payments: SchedulePayment[];
  paid: number;
  balance_owing: number;
}

export interface SchedulesResponse {
  as_of: string;
  schedules: RecurringSchedule[];
  total_expected: number;
  total_paid: number;
  total_owing: number;
}

export interface SharedExpensesResponse {
  items: SharedExpenseItem[];
  total_shared: number;
  total_settled: number;
  balance_owing: number;
  by_category: SharedGroup[];
  by_tag: SharedGroup[];
}

export interface EconomicsSummary {
  year: string;
  cpi: {
    current_index: number | null;
    yoy_change: number | null;
    base_year_index: number | null;
    base_year: string;
  };
  spending_power: {
    salary: number;
    salary_real: number | null;
    salary_real_prev_year: number | null;
    purchasing_power_loss: number | null;
    monthly_expenses_nominal: number;
    monthly_expenses_real: number | null;
    real_savings_rate: number | null;
  };
  tax_analysis: {
    gross_income: number;
    taxable_income: number;
    payg: number;
    medicare: number;
    total_tax: number;
    effective_rate: number;
    after_tax: number;
    after_tax_real: number | null;
    tax_breakdown: { category: string; amount: number; pct: number }[];
  };
  inflation_adjusted_spending: {
    category: string;
    nominal: number;
    real: number | null;
    real_prev_year: number | null;
    real_change_pct: number | null;
  }[];
  net_worth: {
    nominal: number;
    real: number | null;
    real_prev_year: number | null;
    real_change_pct: number | null;
  };
  cpi_history: { period: string; index_value: number; pct_change_yoy: number | null }[];
}

export const api = {
  transactions: (params?: Record<string, string>) =>
    get<Transaction[]>(`${BASE}/transactions`, params),

  updateTransaction: (id: number, data: Record<string, unknown>) =>
    patch<{ ok: boolean }>(`/transactions/${id}`, data),

  categories: () => get<Category[]>(`${BASE}/categories`),

  accounts: () => get<Account[]>(`${BASE}/accounts`),

  accountsSummary: () => get<AccountsSummaryResponse>(`${BASE}/accounts/summary`),

  monthlySummary: (year?: string, params?: Record<string, string>) =>
    get<MonthlySummary[]>(`${BASE}/summary/monthly`, { ...params, ...(year ? { year } : {}) }),

  categorySummary: (from?: string, to?: string, params?: Record<string, string>) =>
    get<CategorySummary[]>(`${BASE}/summary/category`, { from: from || "", to: to || "", ...params }),

  budgetVsActual: (month?: string, params?: Record<string, string>) =>
    get<BudgetItem[]>(`${BASE}/budget-vs-actual`, { ...(month ? { month } : {}), ...params }),

  trends: (from?: string, to?: string, params?: Record<string, string>) =>
    get<TrendPoint[]>(`${BASE}/summary/trends`, { from: from || "", to: to || "", ...params }),

  taxSummary: (fy?: string) =>
    get<TaxSummary>(`${BASE}/summary/tax`, fy ? { fy } : undefined),

  topMerchants: (year?: string) =>
    get<TopMerchant[]>(`${BASE}/summary/top-merchants`, year ? { year } : undefined),

  yearReview: (year?: string) =>
    get<YearReview>(`${BASE}/summary/year-review`, year ? { year } : undefined),

  spreadsheetOutgoing: (fy?: string) =>
    get<SpreadsheetOutgoing[]>(`${BASE}/spreadsheet/outgoing`, fy ? { fy } : undefined),

  spreadsheetIncoming: (fy?: string) =>
    get<SpreadsheetIncoming[]>(`${BASE}/spreadsheet/incoming`, fy ? { fy } : undefined),

  spreadsheetRental: (fy?: string) =>
    get<RentalProperty[]>(`${BASE}/spreadsheet/rental`, fy ? { fy } : undefined),

  spreadsheetWorkTrips: (fy?: string) =>
    get<WorkTripsResponse>(`${BASE}/spreadsheet/work-trips`, fy ? { fy } : undefined),

  atoReturn: (fy?: string) =>
    get<ATOReturn>(`${BASE}/ato/return`, fy ? { fy } : undefined),

  atoLodged: () =>
    get<ATOLodgedResponse>(`${BASE}/ato/lodged`),

  depreciation: () =>
    get<DepreciationResponse>(`${BASE}/depreciation`),

  updateSplit: (txnId: number, data: { business_name: string; business_pct: number }) =>
    patch<{ ok: boolean }>(`/transactions/${txnId}/split`, data),

  sharedExpenses: () =>
    get<SharedExpensesResponse>(`${BASE}/shared-expenses`),

  addSharedExpense: (transactionId: number, splitPct?: number) =>
    post<{ ok: boolean }>("/shared-expenses", {
      transaction_id: transactionId,
      ...(splitPct !== undefined ? { split_pct: splitPct } : {}),
    }),

  updateSharedExpense: (id: number, data: { is_settled?: boolean; split_pct?: number }) =>
    patch<{ ok: boolean }>(`/shared-expenses/${id}`, data),

  deleteSharedExpense: (id: number) =>
    del<{ ok: boolean }>(`/shared-expenses/${id}`),

  recurringSchedules: () =>
    get<SchedulesResponse>(`${BASE}/schedules`),

  economicsSummary: (year?: string) =>
    get<EconomicsSummary>(`${BASE}/summary/economics`, year ? { year } : undefined),

  syncCpi: () =>
    post<{ ok: boolean; rows_synced: number }>("/cpi/sync", {}),
};
