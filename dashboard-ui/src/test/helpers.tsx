import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Reply = unknown | [number, unknown];
type Handler = (call: Call) => Reply;

/** Fake the API: `routes` maps "GET /api/..." (exact path, without the query) to a reply. */
export function mockApi(routes: Record<string, Reply | Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://127.0.0.1:8765");
      const method = (init?.method ?? "GET").toUpperCase();
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
      const call = { method, path: url.pathname, body };
      calls.push(call);
      const route = routes[`${method} ${url.pathname}`];
      if (route === undefined) {
        return new Response(JSON.stringify({ error: `no fake for ${method} ${url.pathname}` }), {
          status: 404,
        });
      }
      const reply = typeof route === "function" ? (route as Handler)(call) : route;
      const [status, payload] = Array.isArray(reply) && typeof reply[0] === "number" ? reply : [200, reply];
      return new Response(JSON.stringify(payload), {
        status: status as number,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false, staleTime: Infinity } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
