import { keepPreviousData, QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      // Keep the previous figures on screen while a filter change loads, so the
      // layout does not jump to a spinner and back.
      placeholderData: keepPreviousData,
    },
  },
});

export const useAccounts = () => useQuery({ queryKey: ["accounts-summary"], queryFn: api.accountsSummary });
export const useAccountList = () => useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
export const useCategories = () => useQuery({ queryKey: ["categories"], queryFn: api.categories, staleTime: Infinity });

export const usePeriod = (from: string, to: string) =>
  useQuery({ queryKey: ["period", from, to], queryFn: () => api.period(from, to) });

export const useTransactions = (filters: Record<string, string>) =>
  useQuery({ queryKey: ["transactions", filters], queryFn: () => api.transactions(filters) });

export const useBudget = (month: string) =>
  useQuery({ queryKey: ["budget", month], queryFn: () => api.budgetVsActual(month) });

export const useTrends = (from: string, to: string) =>
  useQuery({ queryKey: ["trends", from, to], queryFn: () => api.trends(from, to) });

export const useYearReview = (year: string) =>
  useQuery({ queryKey: ["year-review", year], queryFn: () => api.yearReview(year) });

export const useSchedules = () => useQuery({ queryKey: ["schedules"], queryFn: api.recurringSchedules });
export const useShared = () => useQuery({ queryKey: ["shared"], queryFn: api.sharedExpenses });
export const useAtoReturn = (fy: string) =>
  useQuery({ queryKey: ["ato-return", fy], queryFn: () => api.atoReturn(fy) });
export const useLodged = () => useQuery({ queryKey: ["ato-lodged"], queryFn: api.atoLodged });
export const useDepreciation = () => useQuery({ queryKey: ["depreciation"], queryFn: api.depreciation });
export const useEconomics = (year: string) =>
  useQuery({ queryKey: ["economics", year], queryFn: () => api.economicsSummary(year) });

export const useSpreadsheet = (tab: "outgoing" | "incoming" | "rental" | "work-trips", fy: string) =>
  useQuery({
    queryKey: ["spreadsheet", tab, fy],
    // The four tabs return different shapes. Keeping the previous data while
    // the next loads is only safe for the SAME tab (a year change); carried
    // across tabs, the Rental view was handed transaction rows and crashed.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === tab ? prev : undefined),
    queryFn: (): Promise<unknown> => {
      switch (tab) {
        case "outgoing": return api.spreadsheetOutgoing(fy);
        case "incoming": return api.spreadsheetIncoming(fy);
        case "rental": return api.spreadsheetRental(fy);
        case "work-trips": return api.spreadsheetWorkTrips(fy);
      }
    },
  });

/** Edit a transaction, then refresh every view built from transactions. */
export function useUpdateTransaction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Record<string, unknown> }) => api.updateTransaction(id, data),
    onSuccess: () => {
      for (const key of ["transactions", "period", "budget", "trends", "year-review", "spreadsheet"])
        qc.invalidateQueries({ queryKey: [key] });
    },
  });
}

export function useSharedMutations() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["shared"] });
  return {
    add: useMutation({ mutationFn: (id: number) => api.addSharedExpense(id), onSuccess: done }),
    update: useMutation({
      mutationFn: ({ id, data }: { id: number; data: { is_settled?: boolean; split_pct?: number } }) =>
        api.updateSharedExpense(id, data),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api.deleteSharedExpense(id), onSuccess: done }),
  };
}

export function useSyncCpi() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.syncCpi, onSuccess: () => qc.invalidateQueries({ queryKey: ["economics"] }) });
}
