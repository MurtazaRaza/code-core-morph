import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { keys } from "./api";
import type { RunEvent } from "./types";

const FLUSH_MS = 250;

/** Which cached views an engine event makes stale. */
export function staleKeys(runId: string, event: RunEvent): (readonly unknown[])[] {
  const stale: (readonly unknown[])[] = [keys.run(runId)];
  if (event.type === "run.status") {
    stale.push(keys.runs, keys.plan(runId), keys.report(runId));
  }
  if (event.type === "step.finished") stale.push(keys.plan(runId), keys.graph(runId));
  if (
    event.type === "unit.status" ||
    event.type.startsWith("attempt.") ||
    event.type.startsWith("gate.")
  ) {
    stale.push(keys.plan(runId), keys.graph(runId), keys.report(runId));
    if (event.unit_id) stale.push(keys.unit(runId, event.unit_id));
  }
  return stale;
}

/**
 * Follow a run's event stream (SSE) and refresh the views each event changes. Starts after
 * `startAfter`, the last event the first load showed; the browser resumes by itself after a
 * dropped connection. Returns whether the stream is connected.
 */
export function useRunEvents(runId: string, startAfter: number | undefined, enabled: boolean) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const start = useRef<number | undefined>(undefined);
  if (start.current === undefined && startAfter !== undefined) start.current = startAfter;
  const after = start.current;

  useEffect(() => {
    if (!enabled || after === undefined || typeof EventSource === "undefined") return;
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?after=${after}`);
    const pending = new Map<string, readonly unknown[]>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const flush = () => {
      timer = undefined;
      for (const key of pending.values()) void queryClient.invalidateQueries({ queryKey: key });
      pending.clear();
    };
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = JSON.parse(message.data) as RunEvent;
      } catch {
        return;
      }
      for (const key of staleKeys(runId, event)) pending.set(JSON.stringify(key), key);
      timer ??= setTimeout(flush, FLUSH_MS);
    };
    return () => {
      source.close();
      if (timer !== undefined) clearTimeout(timer);
      setConnected(false);
    };
  }, [runId, after, enabled, queryClient]);

  return connected;
}
