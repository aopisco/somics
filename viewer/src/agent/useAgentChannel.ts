/** Lets an agent drive the viewer over HTTP: pulls patches in, reports state out. */

import { useEffect } from "react";
import { create } from "zustand";

import { useStore, viewerState } from "../state";
import { parseControlMessage, sanitizePatch } from "./protocol";

interface AgentStatus {
  driving: boolean;
  note: string | null;
  actor: string | null;
  revision: number;
  release: () => void;
}

export const useAgentStatus = create<AgentStatus>((set) => ({
  driving: false,
  note: null,
  actor: null,
  revision: 0,
  release: () => set({ driving: false }),
}));

const DRIVING_TIMEOUT_MS = 6_000;
const REPORT_THROTTLE_MS = 1_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
const MAX_CONSECUTIVE_FAILURES = 6;

export function useAgentChannel(): void {
  useEffect(() => {
    if (typeof EventSource === "undefined") return;

    let source: EventSource | null = null;
    let drivingTimer: ReturnType<typeof setTimeout> | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let lastRevision = -Infinity;
    let failures = 0;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource("/api/control/stream");
      source = es;

      es.onmessage = (event) => {
        const message = parseControlMessage(event.data);
        if (!message || message.revision <= lastRevision) return;
        lastRevision = message.revision;
        failures = 0;

        const merged = { ...viewerState(useStore.getState()), ...sanitizePatch(message.patch) };
        useStore.getState().hydrate(merged);
        useAgentStatus.setState({
          driving: true,
          note: message.note,
          actor: message.actor,
          revision: message.revision,
        });

        clearTimeout(drivingTimer);
        drivingTimer = setTimeout(() => useAgentStatus.setState({ driving: false }), DRIVING_TIMEOUT_MS);
      };

      es.onerror = () => {
        es.close();
        if (source === es) source = null;
        failures += 1;
        if (failures > MAX_CONSECUTIVE_FAILURES || cancelled) return;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** (failures - 1), RECONNECT_MAX_MS);
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    // Report our own state back so the backend (and other viewers) see what happened
    // after a patch was applied. Inbound patch -> hydrate -> this subscription firing
    // -> outbound PUT is expected; the throttle + changed-check below keeps that loop
    // from becoming a per-frame flood.
    let lastReported = "";
    let lastReportedAt = 0;
    let reportTimer: ReturnType<typeof setTimeout> | undefined;

    const report = () => {
      const body = JSON.stringify(viewerState(useStore.getState()));
      if (body === lastReported) return;
      const wait = REPORT_THROTTLE_MS - (Date.now() - lastReportedAt);
      if (wait > 0) {
        clearTimeout(reportTimer);
        reportTimer = setTimeout(report, wait);
        return;
      }
      lastReported = body;
      lastReportedAt = Date.now();
      // Without an explicit JSON content type fetch sends text/plain and the
      // endpoint's Body(dict) rejects the report with a 422.
      fetch("/api/control/state", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body,
      }).catch(() => {});
    };

    const unsubscribe = useStore.subscribe(report);

    return () => {
      cancelled = true;
      source?.close();
      unsubscribe();
      clearTimeout(drivingTimer);
      clearTimeout(reconnectTimer);
      clearTimeout(reportTimer);
    };
  }, []);
}
