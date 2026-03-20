import { api, type Category, type Account } from "./api";

export async function populateFilters() {
  const [categories, accounts] = await Promise.all([
    api.categories(),
    api.accounts(),
  ]);

  populateCategorySelect("txn-category", categories);
  populateAccountSelect("txn-account", accounts);
  populateYearSelect("dash-year");
  setDefaultMonth("budget-month");
}

function populateCategorySelect(id: string, categories: Category[]) {
  const sel = document.getElementById(id) as HTMLSelectElement | null;
  if (!sel) return;
  // Keep the "All" option
  categories.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = c.name;
    sel.appendChild(opt);
  });
}

function populateAccountSelect(id: string, accounts: Account[]) {
  const sel = document.getElementById(id) as HTMLSelectElement | null;
  if (!sel) return;
  accounts.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  });
}

function populateYearSelect(id: string) {
  const sel = document.getElementById(id) as HTMLSelectElement | null;
  if (!sel) return;
  const currentYear = new Date().getFullYear();
  for (let y = currentYear; y >= currentYear - 7; y--) {
    const opt = document.createElement("option");
    opt.value = String(y);
    opt.textContent = String(y);
    sel.appendChild(opt);
  }
  sel.value = String(currentYear);
}

function setDefaultMonth(id: string) {
  const input = document.getElementById(id) as HTMLInputElement | null;
  if (!input) return;
  const now = new Date();
  input.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function getTransactionFilters(): Record<string, string> {
  return {
    from: (document.getElementById("txn-from") as HTMLInputElement)?.value || "",
    to: (document.getElementById("txn-to") as HTMLInputElement)?.value || "",
    category: (document.getElementById("txn-category") as HTMLSelectElement)?.value || "",
    account: (document.getElementById("txn-account") as HTMLSelectElement)?.value || "",
    search: (document.getElementById("txn-search") as HTMLInputElement)?.value || "",
    limit: "200",
  };
}
