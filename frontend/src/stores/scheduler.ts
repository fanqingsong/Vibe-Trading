import { create } from "zustand";
import {
  api,
  ApiError,
  isAuthRequiredError,
  type SchedulePreset,
  type ScheduledTask,
} from "@/lib/api";

// Bundled preset fallback. The backend's `/scheduler/presets` endpoint serves
// the authoritative list, but we seed the store with these so the schedule
// picker is never empty — even before the API resolves, when it transiently
// fails, or in offline/CI environments. Keep the keys in sync with
// `PRESET_TO_CRON` / `PRESET_LABELS` in `agent/src/scheduler/cron.py`.
const DEFAULT_PRESETS: SchedulePreset[] = [
  { key: "every_minute", label: "Every minute" },
  { key: "every_5_minutes", label: "Every 5 minutes" },
  { key: "every_15_minutes", label: "Every 15 minutes" },
  { key: "every_30_minutes", label: "Every 30 minutes" },
  { key: "hourly", label: "Every hour (at :00)" },
  { key: "daily_0000", label: "Daily at 00:00" },
  { key: "daily_0930", label: "Daily at 09:30" },
  { key: "daily_1500", label: "Daily at 15:00" },
  { key: "weekdays_0930", label: "Weekdays at 09:30" },
  { key: "weekdays_1600", label: "Weekdays at 16:00" },
  { key: "weekly_mon_0930", label: "Every Monday at 09:30" },
  { key: "monthly_1st_0000", label: "Monthly on the 1st at 00:00" },
];

interface SchedulerState {
  tasks: ScheduledTask[];
  presets: SchedulePreset[];
  loading: boolean;
  error: string | null;
  runningActionId: string | null;

  loadTasks: () => Promise<void>;
  loadPresets: () => Promise<void>;
  createTask: (body: import("@/lib/api").ScheduledTaskCreateRequest) => Promise<ScheduledTask | null>;
  updateTask: (id: string, body: import("@/lib/api").ScheduledTaskUpdateRequest) => Promise<ScheduledTask | null>;
  deleteTask: (id: string) => Promise<boolean>;
  toggleTask: (id: string) => Promise<ScheduledTask | null>;
  runNow: (id: string) => Promise<{ status: string; attempt_id?: string; reason?: string } | null>;
  clearError: () => void;
}

function fmtError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "unknown error";
}

export const useSchedulerStore = create<SchedulerState>((set, get) => ({
  tasks: [],
  presets: DEFAULT_PRESETS,
  loading: false,
  error: null,
  runningActionId: null,

  loadTasks: async () => {
    set({ loading: true, error: null });
    try {
      const resp = await api.listScheduledTasks();
      set({ tasks: resp.tasks ?? [], loading: false });
    } catch (err) {
      if (isAuthRequiredError(err)) {
        set({ loading: false });
        return;
      }
      set({ loading: false, error: fmtError(err) });
    }
  },

  loadPresets: async () => {
    try {
      const resp = await api.listSchedulePresets();
      set({ presets: resp.presets ?? DEFAULT_PRESETS });
    } catch {
      // Presets are a convenience; fall back to the bundled defaults so the
      // picker is never empty even when the API is unreachable or slow.
      set({ presets: DEFAULT_PRESETS });
    }
  },

  createTask: async (body) => {
    set({ error: null, runningActionId: "new" });
    try {
      const resp = await api.createScheduledTask(body);
      const task = resp.task;
      set((s) => ({ tasks: [task, ...s.tasks], runningActionId: null }));
      return task;
    } catch (err) {
      set({ runningActionId: null, error: fmtError(err) });
      return null;
    }
  },

  updateTask: async (id, body) => {
    set({ error: null, runningActionId: id });
    try {
      const resp = await api.updateScheduledTask(id, body);
      const task = resp.task;
      set((s) => ({
        tasks: s.tasks.map((t) => (t.id === id ? task : t)),
        runningActionId: null,
      }));
      return task;
    } catch (err) {
      set({ runningActionId: null, error: fmtError(err) });
      return null;
    }
  },

  deleteTask: async (id) => {
    set({ error: null, runningActionId: id });
    try {
      await api.deleteScheduledTask(id);
      set((s) => ({
        tasks: s.tasks.filter((t) => t.id !== id),
        runningActionId: null,
      }));
      return true;
    } catch (err) {
      set({ runningActionId: null, error: fmtError(err) });
      return false;
    }
  },

  toggleTask: async (id) => {
    set({ error: null, runningActionId: id });
    try {
      const resp = await api.toggleScheduledTask(id);
      const task = resp.task;
      set((s) => ({
        tasks: s.tasks.map((t) => (t.id === id ? task : t)),
        runningActionId: null,
      }));
      return task;
    } catch (err) {
      set({ runningActionId: null, error: fmtError(err) });
      return null;
    }
  },

  runNow: async (id) => {
    set({ error: null, runningActionId: id });
    try {
      const resp = await api.runScheduledTaskNow(id);
      set({ runningActionId: null });
      // Refresh the task to pick up last_run_at / last_status.
      void get().loadTasks();
      return resp.result;
    } catch (err) {
      set({ runningActionId: null, error: fmtError(err) });
      return null;
    }
  },

  clearError: () => set({ error: null }),
}));
