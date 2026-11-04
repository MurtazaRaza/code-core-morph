// A small history-API router: the dashboard has a handful of routes and needs no more.
import { type AnchorHTMLAttributes, type MouseEvent, useMemo, useSyncExternalStore } from "react";

export const RUN_TABS = ["overview", "plan", "graph", "report"] as const;
export type RunTab = (typeof RUN_TABS)[number];

export type Route =
  | { name: "runs" }
  | { name: "run"; runId: string; tab: RunTab }
  | { name: "unit"; runId: string; unitId: string; attempt: number | undefined }
  | { name: "notFound"; path: string };

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

const snapshot = () => window.location.pathname + window.location.search;

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (to === snapshot()) return;
  if (options.replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  for (const listener of listeners) listener();
}

export function parseRoute(href: string): Route {
  const url = new URL(href, "http://dashboard.invalid");
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts.length === 0) return { name: "runs" };
  const [first, runId, section, unitId] = parts;
  if (first === "runs" && runId) {
    if (parts.length === 2) return { name: "run", runId, tab: "overview" };
    if (parts.length === 3 && (RUN_TABS as readonly string[]).includes(section ?? "")) {
      return { name: "run", runId, tab: section as RunTab };
    }
    if (parts.length === 4 && section === "units" && unitId) {
      const attempt = Number(url.searchParams.get("attempt"));
      return {
        name: "unit",
        runId,
        unitId: unitId.toUpperCase(),
        attempt: Number.isInteger(attempt) && attempt > 0 ? attempt : undefined,
      };
    }
  }
  return { name: "notFound", path: url.pathname };
}

export function useRoute(): Route {
  const href = useSyncExternalStore(subscribe, snapshot, snapshot);
  return useMemo(() => parseRoute(href), [href]);
}

const enc = encodeURIComponent;

export const paths = {
  runs: () => "/",
  run: (runId: string, tab: RunTab = "overview") =>
    tab === "overview" ? `/runs/${enc(runId)}` : `/runs/${enc(runId)}/${tab}`,
  unit: (runId: string, unitId: string, attempt?: number) =>
    `/runs/${enc(runId)}/units/${enc(unitId)}` + (attempt ? `?attempt=${attempt}` : ""),
};

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string };

/** An anchor that navigates in place; modified clicks still open new tabs. */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  function handle(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  }
  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
