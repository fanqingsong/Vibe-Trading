import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ScreenJobKind, type ScreenJobStatus } from "@/lib/api";

/**
 * Refresh-survivable background screen (Dividends / Buy Points / Chanlun).
 *
 * Screens run server-side as jobs (``POST /screen/{kind}/jobs`` +
 * ``GET /screen/{kind}/jobs/{id}``). This hook starts a job, remembers it in
 * ``sessionStorage``, and on re-mount re-attaches to the same job — so a page
 * refresh keeps showing "Screening…" with the server-side elapsed time and
 * never restarts (or loses) the background run. A finished job within the
 * server TTL re-hydrates its result instantly.
 */

export type ScreenJobPhase = "idle" | "running" | "done" | "error";

const POLL_INTERVAL_MS = 2000;
const TICK_INTERVAL_MS = 1000;

interface StoredScreenJob {
  jobId: string;
  /** Canonical key of the params the job was started with. */
  paramsKey: string;
  /** Raw params (used by pages to rehydrate filter form state). */
  params: Record<string, unknown>;
  savedAt: number;
}

/** sessionStorage entry for a page's most recent screen job (null if none). */
export function readScreenJobEntry(storageKey: string): StoredScreenJob | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredScreenJob;
    if (!parsed || typeof parsed.jobId !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

/** Params of the page's stored screen job, for filter-state initialization. */
export function readScreenJobParams(storageKey: string): Record<string, unknown> {
  return readScreenJobEntry(storageKey)?.params ?? {};
}

/** Numeric filter value from the stored job's params (fallback when absent). */
export function numScreenParam(
  storageKey: string,
  key: string,
  fallback: number
): number {
  const v = readScreenJobParams(storageKey)[key];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** String filter value from the stored job's params (fallback when absent). */
export function strScreenParam(
  storageKey: string,
  key: string,
  fallback: string
): string {
  const v = readScreenJobParams(storageKey)[key];
  return typeof v === "string" && v ? v : fallback;
}

/** Boolean filter value from the stored job's params (fallback when absent). */
export function boolScreenParam(
  storageKey: string,
  key: string,
  fallback: boolean
): boolean {
  const v = readScreenJobParams(storageKey)[key];
  return typeof v === "boolean" ? v : fallback;
}

/** Canonical params key — must match across starts for job reuse. */
function paramsKeyOf(params: Record<string, unknown>): string {
  const keys = Object.keys(params).sort();
  const parts = keys.map(
    (k) => `${JSON.stringify(k)}:${JSON.stringify(params[k] ?? null)}`
  );
  return `{${parts.join(",")}}`;
}

export interface UseScreenJobOptions {
  kind: ScreenJobKind;
  /** sessionStorage key, unique per page (e.g. "screen:dividends"). */
  storageKey: string;
  /** Current filter params; invoked whenever a new job must be started. */
  buildParams: () => Record<string, unknown>;
}

export interface UseScreenJobResult<T> {
  phase: ScreenJobPhase;
  /** Server-based elapsed seconds of the current/last job, ticking locally. */
  elapsedSec: number;
  data: T | null;
  error: string | null;
  /** True when the current state was re-attached from a stored job. */
  resumed: boolean;
  /** Explicit Screen click: recompute unless an identical run is in flight. */
  start: () => void;
}

export function useScreenJob<T>(options: UseScreenJobOptions): UseScreenJobResult<T> {
  const { kind, storageKey } = options;
  // buildParams closes over ever-changing form state; keep the latest in a ref
  // so start/resume callbacks stay referentially stable.
  const buildParamsRef = useRef(options.buildParams);
  buildParamsRef.current = options.buildParams;

  const [phase, setPhase] = useState<ScreenJobPhase>("idle");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resumed, setResumed] = useState(false);

  const jobIdRef = useRef<string | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const tickTimerRef = useRef<number | null>(null);
  const pollingRef = useRef(false);
  // Server-reported elapsed at the last poll; the local tick extrapolates.
  const baseElapsedRef = useRef(0);
  const baseAtRef = useRef(0);
  const startNewRef = useRef<(opts: { fresh: boolean }) => Promise<void>>(async () => {});

  const stopTimers = useCallback(() => {
    if (pollTimerRef.current != null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (tickTimerRef.current != null) {
      window.clearInterval(tickTimerRef.current);
      tickTimerRef.current = null;
    }
  }, []);

  const applyStatus = useCallback(
    (status: ScreenJobStatus) => {
      baseElapsedRef.current = status.elapsed_seconds;
      baseAtRef.current = Date.now();
      setElapsedSec(Math.floor(status.elapsed_seconds));
      if (status.status === "queued" || status.status === "running") {
        setPhase("running");
      } else if (status.status === "done") {
        setPhase("done");
        setData((status.result as T) ?? null);
        stopTimers();
      } else {
        setPhase("error");
        setError(status.error || "Screen failed");
        stopTimers();
      }
    },
    [stopTimers]
  );

  const poll = useCallback(async () => {
    const jobId = jobIdRef.current;
    if (!jobId || pollingRef.current) return;
    pollingRef.current = true;
    try {
      const status = await api.getScreenJob(kind, jobId);
      if (jobIdRef.current !== jobId) return; // superseded by a newer start
      applyStatus(status);
    } catch (e) {
      if (jobIdRef.current !== jobId) return;
      if (e instanceof ApiError && e.status === 404) {
        // Job evaporated (server restart / TTL eviction) — restart the screen.
        await startNewRef.current({ fresh: false });
      }
      // Other poll failures are treated as transient: keep polling while the
      // background job may still be running.
    } finally {
      pollingRef.current = false;
    }
  }, [kind, applyStatus]);

  const startTimers = useCallback(() => {
    stopTimers();
    pollTimerRef.current = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    tickTimerRef.current = window.setInterval(() => {
      setElapsedSec(
        Math.floor(baseElapsedRef.current + (Date.now() - baseAtRef.current) / 1000)
      );
    }, TICK_INTERVAL_MS);
    void poll();
  }, [poll, stopTimers]);

  const startNew = useCallback(
    async (opts: { fresh: boolean }) => {
      stopTimers();
      setError(null);
      setData(null);
      setResumed(false);
      setPhase("running");
      setElapsedSec(0);
      baseElapsedRef.current = 0;
      baseAtRef.current = Date.now();

      const params = buildParamsRef.current();
      try {
        const started = await api.startScreenJob(kind, params, opts.fresh);
        jobIdRef.current = started.job_id;
        try {
          window.sessionStorage.setItem(
            storageKey,
            JSON.stringify({
              jobId: started.job_id,
              paramsKey: paramsKeyOf(params),
              params,
              savedAt: Date.now(),
            } satisfies StoredScreenJob)
          );
        } catch {
          /* storage unavailable (private mode) — resume is simply disabled */
        }
        startTimers();
      } catch (e) {
        setPhase("error");
        setError(e instanceof Error ? e.message : "Failed to start screen");
      }
    },
    [kind, storageKey, startTimers, stopTimers]
  );
  startNewRef.current = startNew;

  // Auto-load / resume once on enter: re-attach to the stored job so a page
  // refresh keeps the in-flight screen (or its recent result) instead of
  // starting a duplicate run.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const stored = readScreenJobEntry(storageKey);
      if (!stored) {
        await startNewRef.current({ fresh: false });
        return;
      }
      try {
        const status = await api.getScreenJob(kind, stored.jobId);
        if (cancelled) return;
        jobIdRef.current = stored.jobId;
        setResumed(true);
        applyStatus(status);
        if (status.status === "queued" || status.status === "running") {
          startTimers();
        }
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          // Job gone (server restart / TTL) — auto-start with current params.
          await startNewRef.current({ fresh: false });
        } else {
          setPhase("error");
          setError(e instanceof Error ? e.message : "Failed to resume screen");
        }
      }
    })();
    return () => {
      cancelled = true;
      stopTimers();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = useCallback(() => {
    void startNew({ fresh: true });
  }, [startNew]);

  return { phase, elapsedSec, data, error, resumed, start };
}
