import { useEffect, useMemo, useState } from "react";
import {
  Clock,
  Plus,
  Play,
  Pencil,
  Trash2,
  Power,
  X,
  Loader2,
  Calendar,
  AlertCircle,
  CheckCircle2,
  PauseCircle,
  Timer,
  Mail,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  type OverlapPolicy,
  type SchedulePreset,
  type ScheduleSpec,
  type ScheduleType,
  type ScheduledTask,
  type TaskStatusCode,
} from "@/lib/api";
import { useSchedulerStore } from "@/stores/scheduler";

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_META: Record<TaskStatusCode, { label: string; cls: string; icon: typeof Clock }> = {
  idle: { label: "Idle", cls: "bg-muted text-muted-foreground", icon: PauseCircle },
  running: { label: "Running", cls: "bg-blue-500/15 text-blue-600 dark:text-blue-400", icon: Loader2 },
  success: { label: "Success", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400", icon: CheckCircle2 },
  failed: { label: "Failed", cls: "bg-red-500/15 text-red-600 dark:text-red-400", icon: AlertCircle },
  skipped: { label: "Skipped", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400", icon: Timer },
};

function StatusBadge({ status }: { status: TaskStatusCode }) {
  const meta = STATUS_META[status] ?? STATUS_META.idle;
  const Icon = meta.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", meta.cls)}>
      <Icon className={cn("h-3 w-3", status === "running" && "animate-spin")} />
      {meta.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Next-run countdown
// ---------------------------------------------------------------------------

function useCountdown(targetIso?: string | null): string {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return useMemo(() => {
    if (!targetIso) return "—";
    const target = new Date(targetIso).getTime();
    const delta = target - now;
    if (delta <= 0) return "due";
    const sec = Math.floor(delta / 1000);
    const min = Math.floor(sec / 60);
    const hr = Math.floor(min / 60);
    const day = Math.floor(hr / 24);
    if (day > 0) return `${day}d ${hr % 24}h`;
    if (hr > 0) return `${hr}h ${min % 60}m`;
    if (min > 0) return `${min}m ${sec % 60}s`;
    return `${sec}s`;
  }, [targetIso, now]);
}

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Form modal
// ---------------------------------------------------------------------------

interface FormState {
  title: string;
  prompt: string;
  scheduleType: ScheduleType;
  preset: string;
  cron: string;
  timezone: string;
  onOverlap: OverlapPolicy;
  notifyEnabled: boolean;
  notifyEmails: string;
}

const DEFAULT_TZ = "Asia/Shanghai";

function emptyForm(): FormState {
  return {
    title: "",
    prompt: "",
    scheduleType: "preset",
    preset: "daily_0930",
    cron: "0 9 * * *",
    timezone: DEFAULT_TZ,
    onOverlap: "skip",
    notifyEnabled: false,
    notifyEmails: "",
  };
}

function formFromTask(t: ScheduledTask): FormState {
  return {
    title: t.title,
    prompt: t.prompt,
    scheduleType: t.schedule_type,
    preset: t.schedule_preset ?? "daily_0930",
    cron: t.cron_expr ?? "0 9 * * *",
    timezone: t.timezone || DEFAULT_TZ,
    onOverlap: t.on_overlap,
    notifyEnabled: t.notify_enabled ?? false,
    notifyEmails: t.notify_emails ?? "",
  };
}

const COMMON_TIMEZONES = [
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "UTC",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/London",
];

function TaskFormModal({
  open,
  initial,
  presets,
  editing,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initial: FormState;
  presets: SchedulePreset[];
  editing: ScheduledTask | null;
  onClose: () => void;
  onSubmit: (state: FormState) => Promise<boolean>;
}) {
  const [form, setForm] = useState<FormState>(initial);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setForm(initial);
      setError(null);
    }
  }, [open, initial]);

  if (!open) return null;

  const fieldClass = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary";
  const labelClass = "block text-xs font-medium text-muted-foreground mb-1";

  const submit = async () => {
    if (!form.title.trim() || !form.prompt.trim()) {
      setError("Title and prompt are required.");
      return;
    }
    if (form.scheduleType === "preset" && !form.preset) {
      setError("Pick a preset or switch to cron.");
      return;
    }
    if (form.scheduleType === "cron" && !form.cron.trim()) {
      setError("Cron expression is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    const ok = await onSubmit(form);
    setSubmitting(false);
    if (ok) onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-lg border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between border-b bg-card px-5 py-3">
          <h2 className="text-base font-semibold">
            {editing ? "Edit Scheduled Task" : "New Scheduled Task"}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-muted" title="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <div>
            <label className={labelClass}>Title</label>
            <input
              className={fieldClass}
              value={form.title}
              onChange={(e) => setForm((s) => ({ ...s, title: e.target.value }))}
              placeholder="e.g. Daily NVDA summary"
              maxLength={255}
            />
          </div>

          <div>
            <label className={labelClass}>Prompt (sent to the agent each fire)</label>
            <textarea
              className={cn(fieldClass, "min-h-[120px] font-mono text-xs resize-y")}
              value={form.prompt}
              onChange={(e) => setForm((s) => ({ ...s, prompt: e.target.value }))}
              placeholder={"Summarize today's NVDA price action, key news, and analyst ratings. Be concise."}
            />
          </div>

          <div className="rounded-md border border-border p-3 space-y-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setForm((s) => ({ ...s, scheduleType: "preset" }))}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                  form.scheduleType === "preset"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/70"
                )}
              >
                Preset
              </button>
              <button
                type="button"
                onClick={() => setForm((s) => ({ ...s, scheduleType: "cron" }))}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                  form.scheduleType === "cron"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/70"
                )}
              >
                Cron
              </button>
            </div>

            {form.scheduleType === "preset" ? (
              <div>
                <label className={labelClass}>Preset</label>
                <select
                  className={fieldClass}
                  value={form.preset}
                  onChange={(e) => setForm((s) => ({ ...s, preset: e.target.value }))}
                >
                  {presets.map((p) => (
                    <option key={p.key} value={p.key}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <label className={labelClass}>Cron expression (5 fields: min hour day month weekday)</label>
                <input
                  className={cn(fieldClass, "font-mono")}
                  value={form.cron}
                  onChange={(e) => setForm((s) => ({ ...s, cron: e.target.value }))}
                  placeholder="*/30 * * * *"
                />
                <div className="mt-2 flex flex-wrap gap-1">
                  {[
                    { label: "Every 30m", v: "*/30 * * * *" },
                    { label: "Hourly", v: "0 * * * *" },
                    { label: "Daily 9:30", v: "30 9 * * *" },
                    { label: "Weekdays 16:00", v: "0 16 * * 1-5" },
                    { label: "Mon 9:30", v: "30 9 * * 1" },
                  ].map((tpl) => (
                    <button
                      key={tpl.label}
                      type="button"
                      onClick={() => setForm((s) => ({ ...s, cron: tpl.v }))}
                      className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                    >
                      {tpl.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelClass}>Timezone</label>
                <select
                  className={fieldClass}
                  value={form.timezone}
                  onChange={(e) => setForm((s) => ({ ...s, timezone: e.target.value }))}
                >
                  {COMMON_TIMEZONES.map((tz) => (
                    <option key={tz} value={tz}>
                      {tz}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelClass}>On overlap</label>
                <select
                  className={fieldClass}
                  value={form.onOverlap}
                  onChange={(e) => setForm((s) => ({ ...s, onOverlap: e.target.value as OverlapPolicy }))}
                >
                  <option value="skip">Skip (recommended)</option>
                  <option value="queue">Queue (coming soon)</option>
                  <option value="replace">Replace (coming soon)</option>
                </select>
              </div>
            </div>
          </div>

          <div className="rounded-md border border-border p-3 space-y-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={form.notifyEnabled}
                onChange={(e) => setForm((s) => ({ ...s, notifyEnabled: e.target.checked }))}
                className="h-4 w-4 rounded border-border"
              />
              <span className="flex items-center gap-1.5 text-sm font-medium">
                <Mail className="h-3.5 w-3.5" />
                Email me the agent's response after each fire
              </span>
            </label>
            {form.notifyEnabled && (
              <div>
                <label className={labelClass}>
                  Recipients (optional, comma-separated; leave blank to use your account email)
                </label>
                <input
                  className={fieldClass}
                  value={form.notifyEmails}
                  onChange={(e) => setForm((s) => ({ ...s, notifyEmails: e.target.value }))}
                  placeholder="you@example.com, ops@example.com"
                  maxLength={512}
                />
              </div>
            )}
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}
        </div>

        <div className="sticky bottom-0 flex items-center justify-end gap-2 border-t bg-card px-5 py-3">
          <button
            onClick={onClose}
            className="rounded-md border px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={submitting}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {editing ? "Save changes" : "Create task"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task row / card
// ---------------------------------------------------------------------------

function TaskRow({
  task,
  onEdit,
  onDelete,
  onToggle,
  onRun,
}: {
  task: ScheduledTask;
  onEdit: (t: ScheduledTask) => void;
  onDelete: (t: ScheduledTask) => void;
  onToggle: (t: ScheduledTask) => void;
  onRun: (t: ScheduledTask) => void;
}) {
  const countdown = useCountdown(task.enabled ? task.next_run_at : null);
  const busy = false; // per-row busy state could be wired via store.runningActionId
  const disabled = !task.enabled;

  return (
    <div className={cn("rounded-lg border bg-card p-4 transition-opacity", disabled && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-medium">{task.title}</h3>
            <StatusBadge status={task.last_status} />
            {disabled && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">disabled</span>
            )}
            {task.notify_enabled && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-blue-500/15 px-2 py-0.5 text-xs text-blue-600 dark:text-blue-400"
                title={task.notify_emails ? `Notify: ${task.notify_emails}` : "Email notifications on"}
              >
                <Mail className="h-3 w-3" />
                {task.notify_emails ? "custom" : "email"}
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{task.prompt}</p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            onClick={() => onRun(task)}
            disabled={busy}
            className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Run now"
          >
            <Play className="h-4 w-4" />
          </button>
          <button
            onClick={() => onToggle(task)}
            disabled={busy}
            className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            title={task.enabled ? "Disable" : "Enable"}
          >
            <Power className={cn("h-4 w-4", task.enabled && "text-emerald-500")} />
          </button>
          <button
            onClick={() => onEdit(task)}
            className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Edit"
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            onClick={() => onDelete(task)}
            className="rounded p-1.5 text-muted-foreground hover:bg-red-500/10 hover:text-red-500"
            title="Delete"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
        <div>
          <div className="text-muted-foreground/70">Schedule</div>
          <div className="truncate" title={task.schedule_label ?? ""}>
            {task.schedule_label ?? "—"}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground/70">Next run</div>
          <div className="font-mono">{task.enabled ? countdown : "—"}</div>
        </div>
        <div>
          <div className="text-muted-foreground/70">Last run</div>
          <div className="truncate" title={formatTime(task.last_run_at)}>
            {formatTime(task.last_run_at)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground/70">Runs</div>
          <div>{task.run_count}</div>
        </div>
      </div>

      {task.last_error && (
        <div className="mt-2 truncate rounded bg-red-500/10 px-2 py-1 text-xs text-red-600 dark:text-red-400" title={task.last_error}>
          {task.last_error}
        </div>
      )}

      {task.last_summary && (
        <div
          className="mt-2 max-h-24 overflow-y-auto rounded bg-muted/40 px-2 py-1 text-xs text-muted-foreground whitespace-pre-wrap"
          title={task.last_summary}
        >
          {task.last_summary}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function Scheduler() {
  const store = useSchedulerStore();
  const { tasks, presets, loading, error, runningActionId } = store;
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduledTask | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [deleteTarget, setDeleteTarget] = useState<ScheduledTask | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    void store.loadTasks();
    void store.loadPresets();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh task list every 15s so the next-run countdowns / statuses stay live.
  useEffect(() => {
    const id = window.setInterval(() => {
      void store.loadTasks();
    }, 15000);
    return () => window.clearInterval(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3500);
  };

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm());
    setModalOpen(true);
  };

  const openEdit = (t: ScheduledTask) => {
    setEditing(t);
    setForm(formFromTask(t));
    setModalOpen(true);
  };

  const submitForm = async (state: FormState): Promise<boolean> => {
    const schedule: ScheduleSpec = {
      type: state.scheduleType,
      preset: state.scheduleType === "preset" ? state.preset : null,
      cron: state.scheduleType === "cron" ? state.cron : null,
      timezone: state.timezone,
    };
    if (editing) {
      const updated = await store.updateTask(editing.id, {
        title: state.title,
        prompt: state.prompt,
        schedule,
        on_overlap: state.onOverlap,
        timezone: state.timezone,
        notify_enabled: state.notifyEnabled,
        notify_emails: state.notifyEmails || undefined,
      });
      if (updated) {
        showToast(`Updated "${updated.title}"`);
        return true;
      }
      return false;
    }
    const created = await store.createTask({
      title: state.title,
      prompt: state.prompt,
      schedule,
      on_overlap: state.onOverlap,
      notify_enabled: state.notifyEnabled,
      notify_emails: state.notifyEmails || undefined,
    });
    if (created) {
      showToast(`Created "${created.title}"`);
      return true;
    }
    return false;
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const ok = await store.deleteTask(deleteTarget.id);
    if (ok) showToast(`Deleted "${deleteTarget.title}"`);
    setDeleteTarget(null);
  };

  const onToggle = async (t: ScheduledTask) => {
    const updated = await store.toggleTask(t.id);
    if (updated) showToast(updated.enabled ? `Enabled "${updated.title}"` : `Disabled "${updated.title}"`);
  };

  const onRun = async (t: ScheduledTask) => {
    showToast(`Triggering "${t.title}"…`);
    const result = await store.runNow(t.id);
    if (result) {
      if (result.status === "success") {
        showToast(`"${t.title}" completed.`);
        void store.loadTasks();
      } else if (result.status === "skipped") {
        showToast(`"${t.title}" skipped: ${result.reason ?? "overlap"}`);
      } else if (result.status === "failed") {
        showToast(`"${t.title}" failed: ${result.reason ?? "unknown"}`);
        void store.loadTasks();
      }
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Clock className="h-6 w-6 text-primary" />
            Scheduled Tasks
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Describe a prompt and a schedule. Each fire runs the prompt through your agent and records the result on the task.
          </p>
        </div>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          New task
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mt-4 flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => store.clearError()} className="text-xs underline">dismiss</button>
        </div>
      )}

      {/* List */}
      <div className="mt-6 space-y-3">
        {loading && tasks.length === 0 ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-28 rounded-lg bg-muted/50 animate-pulse" />
            ))}
          </div>
        ) : tasks.length === 0 ? (
          <div className="rounded-lg border border-dashed bg-card/50 py-16 text-center">
            <Calendar className="mx-auto h-10 w-10 text-muted-foreground/50" />
            <p className="mt-3 text-sm text-muted-foreground">No scheduled tasks yet.</p>
            <p className="text-xs text-muted-foreground/70">Click “New task” to create your first one.</p>
          </div>
        ) : (
          tasks.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              onEdit={openEdit}
              onDelete={setDeleteTarget}
              onToggle={onToggle}
              onRun={onRun}
            />
          ))
        )}
      </div>

      {/* Form modal */}
      <TaskFormModal
        open={modalOpen}
        initial={form}
        presets={presets}
        editing={editing}
        onClose={() => setModalOpen(false)}
        onSubmit={submitForm}
      />

      {/* Delete confirm */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={() => setDeleteTarget(null)}
        >
          <div
            className="w-full max-w-md rounded-lg border bg-card p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold">Delete task?</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              “{deleteTarget.title}” will be removed from the scheduler.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                disabled={runningActionId === deleteTarget.id}
                className="inline-flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
              >
                {runningActionId === deleteTarget.id && <Loader2 className="h-4 w-4 animate-spin" />}
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
