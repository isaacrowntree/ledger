import { useEffect, useState, type ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";

/**
 * The phone's navigation: a floating glass dock at the bottom, one tab per
 * area, the way an installed app is laid out. Shown only on narrow screens
 * (see .dock in styles.css); wider screens keep the side rail.
 *
 * Native tab behaviour: each tab remembers its scroll position, tapping the
 * current tab again scrolls it to the top, and scrolling down shrinks the
 * dock to icons so it covers less of the page.
 */
export type DockTab = { to: string; label: string; area: string; icon: (filled: boolean) => ReactNode };

const scrollOf = new Map<string, number>();

export function Dock({ tabs, area }: { tabs: DockTab[]; area: string }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [bounce, setBounce] = useState<string | null>(null);
  const active = Math.max(0, tabs.findIndex((t) => t.area === area));
  const minimized = useMinimizeOnScroll(pathname);

  useEffect(() => {
    window.scrollTo({ top: scrollOf.get(pathname) ?? 0 });
  }, [pathname]);

  return (
    <nav className={`dock${minimized ? " min" : ""}`} aria-label="Areas"
         style={{ ["--tabs" as string]: tabs.length }}>
      <span className="dock-lens" aria-hidden style={{ transform: `translateX(${active * 100}%)` }} />
      {tabs.map((t, i) => {
        const current = i === active;
        return (
          <Link key={t.to} to={t.to} aria-current={current ? "page" : undefined}
                className={current ? "current" : undefined}
                onClick={(e) => {
                  scrollOf.set(pathname, window.scrollY);
                  if (current && pathname === t.to) {
                    e.preventDefault();
                    window.scrollTo({ top: 0, behavior: "smooth" });
                    return;
                  }
                  setBounce(t.to);
                  navigator.vibrate?.(8);
                }}>
            <span className={bounce === t.to ? "dock-icon bounce" : "dock-icon"}
                  onAnimationEnd={() => setBounce(null)}>{t.icon(current)}</span>
            <span className="dock-label">{t.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

/** Scrolling down shrinks the dock to icons; scrolling up, or reaching the top, restores it. */
function useMinimizeOnScroll(pathname: string): boolean {
  const [minimized, setMinimized] = useState(false);
  const [shownFor, setShownFor] = useState(pathname);
  if (shownFor !== pathname) {      // a newly opened page starts with the full dock
    setShownFor(pathname);
    setMinimized(false);
  }
  useEffect(() => {
    let lastY = window.scrollY;
    let ticking = false;
    const update = () => {
      ticking = false;
      const y = window.scrollY;
      const delta = y - lastY;
      lastY = y;
      if (y <= 8) setMinimized(false);
      else if (delta > 4) setMinimized(true);
      else if (delta < -4) setMinimized(false);
    };
    const onScroll = () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [pathname]);
  return minimized;
}

const svg = (filled: boolean, children: ReactNode) => (
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden
       fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={filled ? 0 : 1.7}
       strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);

export const ICONS = {
  overview: (f: boolean) => svg(f, <path d="M3 11 12 3l9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" />),
  money: (f: boolean) => f
    ? svg(true, <path fillRule="evenodd" d="M4 6a2 2 0 0 1 2-2h11a1 1 0 0 1 1 1v2h1a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zm12 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM6 6h10V7H6z" />)
    : svg(false, <><path d="M4 7a2 2 0 0 1 2-2h11v3" /><rect x="4" y="7" width="17" height="13" rx="2" /><circle cx="16" cy="13.5" r="1.3" /></>),
  tax: (f: boolean) => f
    ? svg(true, <path fillRule="evenodd" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm2 10v1.6h8V12zm0 4v1.6h6V16zm5-12v4h4z" />)
    : svg(false, <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7z" /><path d="M14 2v5h5M8 13h8M8 17h6" /></>),
  analysis: (f: boolean) => (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden
         strokeWidth={f ? 2.6 : 1.7} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" />
    </svg>
  ),
};
