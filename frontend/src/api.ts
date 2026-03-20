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
    rental_weeks: number;
    gross_income: number;
    income_share: number;
    expenses: { ato_label: string; raw: number; share: number }[];
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

  updateSplit: (txnId: number, data: { business_name: string; business_pct: number }) =>
    patch<{ ok: boolean }>(`/transactions/${txnId}/split`, data),
};
