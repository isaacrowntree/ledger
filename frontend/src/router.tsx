import type { ReactNode } from "react";
import {
  createHashHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import { Overview } from "./views/Overview";
import { Transactions } from "./views/money/Transactions";
import { Budget } from "./views/money/Budget";
import { Recurring } from "./views/money/Recurring";
import { Shared } from "./views/money/Shared";
import { FinancialYear } from "./views/tax/FinancialYear";
import { AtoReturn } from "./views/tax/AtoReturn";
import { Lodgement } from "./views/tax/Lodgement";
import { Depreciation } from "./views/tax/Depreciation";
import { Trends } from "./views/analysis/Trends";
import { YearReview } from "./views/analysis/YearReview";
import { Economics } from "./views/analysis/Economics";

// Four areas instead of twelve tabs: where you stand, the money itself, tax,
// and looking back.
const AREAS = [
  { to: "/", label: "Overview", children: [] as { to: string; label: string }[] },
  {
    to: "/money/transactions",
    label: "Money",
    children: [
      { to: "/money/transactions", label: "Transactions" },
      { to: "/money/budget", label: "Budget" },
      { to: "/money/shared", label: "Shared expenses" },
      { to: "/money/recurring", label: "Recurring" },
    ],
  },
  {
    to: "/tax/return",
    label: "Tax",
    children: [
      { to: "/tax/return", label: "This year's return" },
      { to: "/tax/year", label: "Financial year detail" },
      { to: "/tax/lodgement", label: "Lodged returns" },
      { to: "/tax/depreciation", label: "Depreciation" },
    ],
  },
  {
    to: "/analysis/trends",
    label: "Analysis",
    children: [
      { to: "/analysis/trends", label: "Trends" },
      { to: "/analysis/review", label: "Year in review" },
      { to: "/analysis/economics", label: "Tax & inflation" },
    ],
  },
];

function Shell() {
  return (
    <div className="shell">
      <nav className="rail" aria-label="Sections">
        <div className="wordmark">Ledger</div>
        {AREAS.map((a) => (
          <div className="rail-area" key={a.label}>
            <Link to={a.to} activeOptions={{ exact: a.to === "/", includeSearch: false }}>{a.label}</Link>
            {a.children.length > 0 && (
              <ul>
                {a.children.map((c) => (
                  <li key={c.to}><Link to={c.to}>{c.label}</Link></li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}

const root = createRootRoute({ component: Shell });
const r = (path: string, component: () => ReactNode) =>
  createRoute({ getParentRoute: () => root, path, component });

const routeTree = root.addChildren([
  r("/", Overview),
  r("/money/transactions", Transactions),
  r("/money/budget", Budget),
  r("/money/shared", Shared),
  r("/money/recurring", Recurring),
  r("/tax/return", AtoReturn),
  r("/tax/year", FinancialYear),
  r("/tax/lodgement", Lodgement),
  r("/tax/depreciation", Depreciation),
  r("/analysis/trends", Trends),
  r("/analysis/review", YearReview),
  r("/analysis/economics", Economics),
  createRoute({ getParentRoute: () => root, path: "/money", beforeLoad: () => { throw redirect({ to: "/money/transactions" }); } }),
  createRoute({ getParentRoute: () => root, path: "/tax", beforeLoad: () => { throw redirect({ to: "/tax/return" }); } }),
  createRoute({ getParentRoute: () => root, path: "/analysis", beforeLoad: () => { throw redirect({ to: "/analysis/trends" }); } }),
]);

export const router = createRouter({
  routeTree,
  history: createHashHistory(),
  defaultNotFoundComponent: () => <p className="lede">That page does not exist. Use the sections on the left.</p>,
});

declare module "@tanstack/react-router" {
  interface Register { router: typeof router }
}
