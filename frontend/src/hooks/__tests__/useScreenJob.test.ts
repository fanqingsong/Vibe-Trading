import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { useScreenJob } from "../useScreenJob";
import { api, ApiError, type ScreenJobStatus } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    startScreenJob: vi.fn(),
    getScreenJob: vi.fn(),
  },
  ApiError: class extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

const mockedStart = vi.mocked(api.startScreenJob);
const mockedGet = vi.mocked(api.getScreenJob);

const STORAGE_KEY = "test:screen:dividends";
const RESULT = { universe: "csi300", results: [{ code: "600036.SH" }] };

function jobStatus(over: Partial<ScreenJobStatus> = {}): ScreenJobStatus {
  return {
    job_id: "job-1",
    kind: "dividends",
    status: "running",
    elapsed_seconds: 0,
    created_at: "2026-08-18T00:00:00Z",
    result: null,
    error: null,
    ...over,
  };
}

function seedStorage(entry: Record<string, unknown> | null) {
  if (entry === null) {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } else {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
  }
}

function renderScreen() {
  return renderHook(() =>
    useScreenJob<{ universe: string; results: unknown[] }>({
      kind: "dividends",
      storageKey: STORAGE_KEY,
      buildParams: () => ({ universe: "csi300", min_yield: 3 }),
    })
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  mockedStart.mockReset();
  mockedGet.mockReset();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useScreenJob", () => {
  it("auto-starts a job (fresh=false) when no stored job exists", async () => {
    seedStorage(null);
    mockedStart.mockResolvedValue({ job_id: "job-1", status: "queued", reused: null });
    // startTimers() kicks an immediate poll, then polls every 2s.
    mockedGet
      .mockResolvedValueOnce(jobStatus({ status: "running", elapsed_seconds: 1 }))
      .mockResolvedValue(
        jobStatus({ status: "done", elapsed_seconds: 3, result: RESULT })
      );

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockedStart).toHaveBeenCalledTimes(1);
    expect(mockedStart).toHaveBeenCalledWith(
      "dividends",
      { universe: "csi300", min_yield: 3 },
      false
    );
    expect(result.current.phase).toBe("running");
    // The job id is persisted so a refresh can re-attach.
    expect(JSON.parse(window.sessionStorage.getItem(STORAGE_KEY)!).jobId).toBe(
      "job-1"
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.phase).toBe("done");
    expect(result.current.data).toEqual(RESULT);
    expect(mockedStart).toHaveBeenCalledTimes(1);
  });

  it("resumes a stored running job without starting a new one", async () => {
    seedStorage({
      jobId: "job-resume",
      paramsKey: "x",
      params: { universe: "csi300", min_yield: 3 },
      savedAt: Date.now(),
    });
    // Mount resume GET → running (5s elapsed); the immediate poll that
    // startTimers() kicks still sees running (6s); the next poll sees done.
    mockedGet
      .mockResolvedValueOnce(
        jobStatus({ job_id: "job-resume", status: "running", elapsed_seconds: 5 })
      )
      .mockResolvedValueOnce(
        jobStatus({ job_id: "job-resume", status: "running", elapsed_seconds: 6 })
      )
      .mockResolvedValue(
        jobStatus({
          job_id: "job-resume",
          status: "done",
          elapsed_seconds: 7,
          result: RESULT,
        })
      );

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockedGet).toHaveBeenCalledWith("dividends", "job-resume");
    expect(mockedStart).not.toHaveBeenCalled();
    expect(result.current.phase).toBe("running");
    expect(result.current.resumed).toBe(true);
    expect(result.current.elapsedSec).toBe(6);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.phase).toBe("done");
    expect(result.current.data).toEqual(RESULT);
  });

  it("hydrates a stored finished job immediately", async () => {
    seedStorage({
      jobId: "job-done",
      paramsKey: "x",
      params: { universe: "csi300", min_yield: 3 },
      savedAt: Date.now(),
    });
    mockedGet.mockResolvedValueOnce(
      jobStatus({
        job_id: "job-done",
        status: "done",
        elapsed_seconds: 42,
        result: RESULT,
      })
    );

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.phase).toBe("done");
    expect(result.current.data).toEqual(RESULT);
    expect(result.current.resumed).toBe(true);
    expect(mockedStart).not.toHaveBeenCalled();
  });

  it("restarts automatically when the stored job is gone (404)", async () => {
    seedStorage({
      jobId: "job-gone",
      paramsKey: "x",
      params: { universe: "csi300", min_yield: 3 },
      savedAt: Date.now(),
    });
    mockedGet.mockRejectedValueOnce(new ApiError("job not found", 404));
    mockedStart.mockResolvedValue({ job_id: "job-new", status: "queued", reused: null });
    mockedGet
      .mockResolvedValueOnce(jobStatus({ job_id: "job-new", status: "running" }))
      .mockResolvedValue(
        jobStatus({ job_id: "job-new", status: "done", result: RESULT })
      );

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockedStart).toHaveBeenCalledTimes(1);
    expect(mockedStart).toHaveBeenCalledWith(
      "dividends",
      { universe: "csi300", min_yield: 3 },
      false
    );
    expect(result.current.phase).toBe("running");
    expect(JSON.parse(window.sessionStorage.getItem(STORAGE_KEY)!).jobId).toBe(
      "job-new"
    );
  });

  it("restarts when a poll mid-screen hits 404 (server restarted)", async () => {
    seedStorage(null);
    mockedStart.mockResolvedValue({ job_id: "job-a", status: "queued", reused: null });
    mockedGet
      .mockResolvedValueOnce(jobStatus({ job_id: "job-a", status: "running" }))
      .mockRejectedValueOnce(new ApiError("job not found", 404));
    mockedStart.mockResolvedValue({ job_id: "job-b", status: "queued", reused: null });
    mockedGet.mockResolvedValue(
      jobStatus({ job_id: "job-b", status: "done", result: RESULT })
    );

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.phase).toBe("running");

    // Next poll (2s later) 404s → hook restarts the screen automatically.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(mockedStart).toHaveBeenCalledTimes(2);
    expect(
      JSON.parse(window.sessionStorage.getItem(STORAGE_KEY)!).jobId
    ).toBe("job-b");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.phase).toBe("done");
  });

  it("start() posts with fresh=true and surfaces start errors", async () => {
    seedStorage(null);
    mockedStart.mockResolvedValue({ job_id: "job-1", status: "queued", reused: null });
    mockedGet.mockResolvedValue(jobStatus({ status: "running" }));

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Explicit Screen click → fresh recompute.
    mockedStart.mockResolvedValue({ job_id: "job-2", status: "queued", reused: null });
    await act(async () => {
      result.current.start();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mockedStart).toHaveBeenLastCalledWith(
      "dividends",
      { universe: "csi300", min_yield: 3 },
      true
    );

    // A failed start lands in the error phase with the message.
    mockedStart.mockRejectedValueOnce(new Error("too many running screens"));
    await act(async () => {
      result.current.start();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.phase).toBe("error");
    expect(result.current.error).toBe("too many running screens");
  });

  it("surfaces job errors from polling", async () => {
    seedStorage({
      jobId: "job-err",
      paramsKey: "x",
      params: {},
      savedAt: Date.now(),
    });
    mockedGet.mockResolvedValueOnce(
      jobStatus({
        job_id: "job-err",
        status: "error",
        error: "tushare rate limited",
      })
    );

    const { result } = renderScreen();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.phase).toBe("error");
    expect(result.current.error).toBe("tushare rate limited");
  });
});
