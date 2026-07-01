import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Database, KeyRound, Loader2, Mail, RotateCcw, Save, Send, Server, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { api, isAuthRequiredError, type DataSourceSettings, type EmailSettings, type LLMProviderOption, type LLMSettings } from "@/lib/api";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";
import { useAuthStore } from "@/stores/auth";

interface LLMFormState {
  provider: string;
  model_name: string;
  base_url: string;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
}

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";
const labelClass = "text-sm font-medium";
const hintClass = "text-xs text-muted-foreground";

function toForm(settings: LLMSettings): LLMFormState {
  return {
    provider: settings.provider,
    model_name: settings.model_name,
    base_url: settings.base_url,
    temperature: settings.temperature,
    timeout_seconds: settings.timeout_seconds,
    max_retries: settings.max_retries,
    reasoning_effort: settings.reasoning_effort || "",
  };
}

export function Settings() {
  const authUser = useAuthStore((s) => s.user);
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const logout = useAuthStore((s) => s.logout);
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [dataSettings, setDataSettings] = useState<DataSourceSettings | null>(null);
  const [form, setForm] = useState<LLMFormState | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [localApiKey, setLocalApiKeyState] = useState(() => getApiAuthKey());
  const [clearApiKey, setClearApiKey] = useState(false);
  const [tushareToken, setTushareToken] = useState("");
  const [clearTushareToken, setClearTushareToken] = useState(false);
  const [ccxtExchange, setCcxtExchange] = useState("binance");
  const [futuHost, setFutuHost] = useState("");
  const [futuPort, setFutuPort] = useState("11111");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dataSaving, setDataSaving] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(null);

  // Email / SMTP settings state.
  const [emailSettings, setEmailSettings] = useState<EmailSettings | null>(null);
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("465");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [clearSmtpPassword, setClearSmtpPassword] = useState(false);
  const [smtpFrom, setSmtpFrom] = useState("");
  const [smtpUseTls, setSmtpUseTls] = useState(true);
  const [smtpRecipients, setSmtpRecipients] = useState("");
  const [emailSaving, setEmailSaving] = useState(false);
  const [emailTesting, setEmailTesting] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([api.getLLMSettings(), api.getDataSourceSettings(), api.getEmailSettings()])
      .then(([llmData, dataSourceData, emailData]) => {
        if (!alive) return;
        setSettings(llmData);
        setForm(toForm(llmData));
        setDataSettings(dataSourceData);
        setEmailSettings(emailData);
        setCcxtExchange(dataSourceData.ccxt_exchange || "binance");
        setFutuHost(dataSourceData.futu_host || "");
        setFutuPort(dataSourceData.futu_port ? String(dataSourceData.futu_port) : "11111");
        setSmtpHost(emailData.host);
        setSmtpPort(String(emailData.port || 465));
        setSmtpUser(emailData.user);
        setSmtpFrom(emailData.from_addr);
        setSmtpUseTls(emailData.use_tls);
        setSmtpRecipients(emailData.recipients.join(", "));
        setSettingsLoadError(null);
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : "Unknown error";
        setSettingsLoadError(message);
        if (isAuthRequiredError(error)) {
          toast.error(message);
        } else {
          toast.error(`Failed to load LLM settings: ${message}`);
          toast.error(`Failed to load data source settings: ${message}`);
          toast.error(`Failed to load email settings: ${message}`);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, []);

  const providers = settings?.providers ?? [];
  const selectedProvider = useMemo<LLMProviderOption | undefined>(
    () => providers.find((provider) => provider.name === form?.provider),
    [form?.provider, providers],
  );

  const applyProviderDefaults = (provider = selectedProvider) => {
    if (!provider || !form) return;
    setForm({
      ...form,
      model_name: provider.default_model,
      base_url: provider.default_base_url,
    });
  };

  const onProviderChange = (name: string) => {
    const provider = providers.find((item) => item.name === name);
    if (!provider || !form) return;
    setForm({
      ...form,
      provider: provider.name,
      model_name: provider.default_model,
      base_url: provider.default_base_url,
    });
    setApiKey("");
    setClearApiKey(false);
  };

  const submitLocalApiKey = (event: FormEvent) => {
    event.preventDefault();
    setApiAuthKey(localApiKey);
    toast.success("Local API key saved");
    window.location.reload();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateLLMSettings({
        ...form,
        api_key: apiKey.trim() || undefined,
        clear_api_key: clearApiKey,
      });
      setSettings(updated);
      setForm(toForm(updated));
      setApiKey("");
      setClearApiKey(false);
      toast.success("LLM settings saved");
    } catch (error) {
      toast.error(`Failed to save LLM settings: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  const submitDataSources = async (event: FormEvent) => {
    event.preventDefault();
    setDataSaving(true);
    try {
      const updated = await api.updateDataSourceSettings({
        tushare_token: tushareToken.trim() || undefined,
        clear_tushare_token: clearTushareToken,
        ccxt_exchange: ccxtExchange.trim() || undefined,
        futu_host: futuHost.trim(),
        futu_port: Number(futuPort) || 0,
      });
      setDataSettings(updated);
      setTushareToken("");
      setClearTushareToken(false);
      setCcxtExchange(updated.ccxt_exchange || "binance");
      setFutuHost(updated.futu_host || "");
      setFutuPort(updated.futu_port ? String(updated.futu_port) : "11111");
      toast.success("Data source settings saved");
    } catch (error) {
      toast.error(`Failed to save data source settings: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setDataSaving(false);
    }
  };

  const submitEmail = async (event: FormEvent) => {
    event.preventDefault();
    setEmailSaving(true);
    try {
      const recipients = smtpRecipients
        .split(/[,;]/)
        .map((r) => r.trim())
        .filter(Boolean);
      const updated = await api.updateEmailSettings({
        host: smtpHost.trim(),
        port: Number(smtpPort) || 465,
        user: smtpUser.trim(),
        password: smtpPassword.trim() || undefined,
        clear_password: clearSmtpPassword,
        use_tls: smtpUseTls,
        from_addr: smtpFrom.trim(),
        recipients,
        notify_trade_alerts: emailSettings?.notify_trade_alerts ?? true,
        notify_reports: emailSettings?.notify_reports ?? true,
      });
      setEmailSettings(updated);
      setSmtpPassword("");
      setClearSmtpPassword(false);
      toast.success("Email settings saved");
    } catch (error) {
      toast.error(`Failed to save email settings: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setEmailSaving(false);
    }
  };

  const testEmail = async () => {
    setEmailTesting(true);
    try {
      const result = await api.testEmailSettings();
      if (result.ok) {
        toast.success(`Test email sent to ${result.recipients.join(", ") || "(self)"} (${result.latency_ms}ms)`);
      } else {
        toast.error(`Test email failed: ${result.message}`);
      }
    } catch (error) {
      toast.error(`Test email failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    } finally {
      setEmailTesting(false);
    }
  };

  const localApiAccessSection = authEnabled && authUser ? (
    <div className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="mb-4 space-y-1">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" />
          <h2 className="text-base font-semibold">{"Account"}</h2>
        </div>
        <p className="text-sm text-muted-foreground">{"You are signed in with the account below."}</p>
      </div>
      <div className="grid gap-1 text-sm">
        <div className="flex justify-between gap-4">
          <span className="text-muted-foreground">Email</span>
          <span className="font-medium">{authUser.email}</span>
        </div>
        {authUser.name && (
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">Name</span>
            <span className="font-medium">{authUser.name}</span>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={() => { logout(); window.location.assign("/login"); }}
        className="mt-4 inline-flex items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-medium transition hover:opacity-80"
      >
        {"Sign out"}
      </button>
    </div>
  ) : (
    <form onSubmit={submitLocalApiKey} className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="mb-4 space-y-1">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" />
          <h2 className="text-base font-semibold">{"Local API access"}</h2>
        </div>
        <p className="text-sm text-muted-foreground">{"For remote or private Web UI deployments, enter the server API key once in this browser. Localhost use can stay blank."}</p>
      </div>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
        <label className="grid gap-2">
          <span className={labelClass}>{"Server API key"}</span>
          <input
            type="password"
            value={localApiKey}
            onChange={(event) => setLocalApiKeyState(event.target.value)}
            className={fieldClass}
            placeholder={"Stored only in this browser. Leave blank to clear it."}
            autoComplete="current-password"
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center justify-center gap-2 self-end rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          <Save className="h-4 w-4" />
          {"Save local key"}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{"Stored only in this browser. Leave blank to clear it."}</p>
    </form>
  );

  if (loading || !form || !settings || !dataSettings || !emailSettings) {
    return (
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{"Settings"}</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{"Configure model credentials and market data source tokens for this local project."}</p>
        </div>
        {localApiAccessSection}
        <div className="flex min-h-32 items-center justify-center rounded-lg border bg-card p-5 text-sm text-muted-foreground">
          {settingsLoadError ? (
            <div className="text-center">
              <div className="font-medium text-foreground">{"Settings are unavailable"}</div>
              <div className="mt-1">{settingsLoadError}</div>
            </div>
          ) : (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {"Loading..."}
            </>
          )}
        </div>
      </div>
    );
  }

  const keyStatus = settings.api_key_configured
    ? "Configured"
    : settings.api_key_required
      ? "Leave blank to keep the current key"
      : selectedProvider?.auth_type === "oauth" && selectedProvider.login_command
        ? `This provider uses OAuth. Run: ${selectedProvider.login_command}`
        : "This provider does not require an API key.";
  const apiKeyDisabled = !selectedProvider?.api_key_required || clearApiKey;
  const tushareStatus = dataSettings.tushare_token_configured
    ? "Configured"
    : "Leave blank to keep the current token";

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{"Settings"}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{"Configure model credentials and market data source tokens for this local project."}</p>
      </div>

      {localApiAccessSection}

      <div className="space-y-2">
        <h2 className="text-lg font-semibold tracking-tight">{"LLM Settings"}</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">{"Choose the model used by the agent; settings are persisted to the system database."}</p>
      </div>

      <form onSubmit={submit} className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)]">
        <section className="rounded-lg border bg-card p-5 shadow-sm">
          <div className="mb-5 flex items-center gap-2">
            <Server className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{"Connection"}</h2>
          </div>

          <div className="grid gap-4">
            <label className="grid gap-2">
              <span className={labelClass}>{"Provider"}</span>
              <select
                value={form.provider}
                onChange={(event) => onProviderChange(event.target.value)}
                className={fieldClass}
              >
                {providers.map((provider) => (
                  <option key={provider.name} value={provider.name}>{provider.label}</option>
                ))}
              </select>
              <span className={hintClass}>{"Changing providers updates the recommended model and endpoint."}</span>
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{"Model"}</span>
              <div className="flex gap-2">
                <input
                  value={form.model_name}
                  onChange={(event) => setForm({ ...form, model_name: event.target.value })}
                  className={fieldClass}
                  required
                />
                <button
                  type="button"
                  onClick={() => applyProviderDefaults()}
                  className="inline-flex shrink-0 items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
                  title={"Use provider defaults"}
                >
                  <RotateCcw className="h-4 w-4" />
                  <span className="hidden sm:inline">{"Use provider defaults"}</span>
                </button>
              </div>
              <span className={hintClass}>{"Use the exact model id required by your provider."}</span>
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{"Base URL"}</span>
              <input
                value={form.base_url}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                className={fieldClass}
                placeholder={selectedProvider?.default_base_url}
                disabled={selectedProvider?.auth_type === "oauth"}
              />
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>
                {selectedProvider?.auth_type === "oauth" ? "OAuth" : "API key"}
              </span>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  className={`${fieldClass} pl-9`}
                  placeholder={keyStatus}
                  autoComplete="current-password"
                  disabled={apiKeyDisabled}
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className={hintClass}>{keyStatus}</span>
                {selectedProvider?.api_key_required ? (
                  <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={clearApiKey}
                      onChange={(event) => {
                        setClearApiKey(event.target.checked);
                        if (event.target.checked) setApiKey("");
                      }}
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    {"Clear saved API key"}
                  </label>
                ) : null}
              </div>
            </label>
          </div>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm">
          <div className="mb-5 flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{"Generation"}</h2>
          </div>

          <div className="grid gap-4">
            <label className="grid gap-2">
              <span className={labelClass}>{"Temperature"}</span>
              <input
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={form.temperature}
                onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })}
                className={fieldClass}
              />
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{"Timeout seconds"}</span>
              <input
                type="number"
                min={1}
                max={3600}
                step={1}
                value={form.timeout_seconds}
                onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })}
                className={fieldClass}
              />
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{"Max retries"}</span>
              <input
                type="number"
                min={0}
                max={20}
                step={1}
                value={form.max_retries}
                onChange={(event) => setForm({ ...form, max_retries: Number(event.target.value) })}
                className={fieldClass}
              />
            </label>

            <label className="grid gap-2">
              <span className={labelClass}>{"Reasoning effort"}</span>
              <select
                value={form.reasoning_effort}
                onChange={(event) => setForm({ ...form, reasoning_effort: event.target.value })}
                className={fieldClass}
              >
                <option value="">{"Off"}</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="max">max</option>
              </select>
              <span className={hintClass}>{"How hard the model thinks before answering. Higher is more thorough but slower; leave Off for fastest replies."}</span>
            </label>

            <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{"Stored in"}: </span>
              <span className="break-all font-mono">{settings.stored_in}</span>
            </div>

            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {saving ? "Saving..." : "Save settings"}
            </button>
          </div>
        </section>
      </form>

      <form onSubmit={submitDataSources} className="rounded-lg border bg-card p-5 shadow-sm">
        <div className="mb-5 space-y-1">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{"Data Source Settings"}</h2>
          </div>
          <p className="text-sm text-muted-foreground">{"Configure optional market data credentials used by backtests and research agents."}</p>
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.9fr)]">
          <div className="grid gap-4">
            <label className="grid gap-2">
              <span className={labelClass}>{"Tushare token"}</span>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="password"
                  value={tushareToken}
                  onChange={(event) => setTushareToken(event.target.value)}
                  className={`${fieldClass} pl-9`}
                  placeholder={tushareStatus}
                  autoComplete="current-password"
                  disabled={clearTushareToken}
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className={hintClass}>{"Used for China A-share, futures, fund, and macro data. If unset, the project falls back to AKShare where available."}</span>
                <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={clearTushareToken}
                    onChange={(event) => {
                      setClearTushareToken(event.target.checked);
                      if (event.target.checked) setTushareToken("");
                    }}
                    className="h-3.5 w-3.5 accent-primary"
                  />
                  {"Clear saved Tushare token"}
                </label>
              </div>
            </label>

            <div className="my-1 border-t pt-4" />

            <label className="grid gap-2">
              <span className={labelClass}>{"Crypto fallback exchange (CCXT)"}</span>
              <input
                value={ccxtExchange}
                onChange={(event) => setCcxtExchange(event.target.value)}
                className={fieldClass}
                placeholder={"binance"}
              />
              <span className={hintClass}>
                {"CCXT exchange id used when OKX is unreachable. Common values: binance, okx, bybit, gate, kraken. Public market data only — no API key required."}
              </span>
            </label>

            <div className="grid gap-4 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,0.6fr)]">
              <label className="grid gap-2">
                <span className={labelClass}>{"Futu OpenD host"}</span>
                <input
                  value={futuHost}
                  onChange={(event) => setFutuHost(event.target.value)}
                  className={fieldClass}
                  placeholder={"127.0.0.1 (leave empty to disable)"}
                />
                <span className={hintClass}>{"FutuOpenD must be running locally for HK / A-share data."}</span>
              </label>
              <label className="grid gap-2">
                <span className={labelClass}>{"Port"}</span>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={futuPort}
                  onChange={(event) => setFutuPort(event.target.value)}
                  className={fieldClass}
                  placeholder={"11111"}
                />
              </label>
            </div>

            <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{"Stored in"}: </span>
              <span className="break-all font-mono">{dataSettings.stored_in}</span>
            </div>

            <button
              type="submit"
              disabled={dataSaving}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {dataSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {dataSaving ? "Saving..." : "Save data source settings"}
            </button>
          </div>

          <div className="rounded-md border bg-muted/20 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="text-sm font-medium">{"BaoStock"}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs ${dataSettings.baostock_supported ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>
                {dataSettings.baostock_supported ? "Loader available" : "No project loader"}
              </span>
            </div>
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>{dataSettings.baostock_message}</p>
              <p>
                {dataSettings.baostock_installed
                  ? "Python package installed"
                  : "Python package not installed"}
              </p>
            </div>
          </div>

          <div className="rounded-md border bg-muted/20 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="text-sm font-medium">{"Futu OpenAPI"}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs ${dataSettings.futu_configured ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}>
                {dataSettings.futu_configured ? "Endpoint set" : "Not configured"}
              </span>
            </div>
            <div className="space-y-1 text-sm text-muted-foreground">
              <p>
                {dataSettings.futu_configured
                  ? `${dataSettings.futu_host}:${dataSettings.futu_port}`
                  : "No endpoint configured."}
              </p>
              <p>{"Requires FutuOpenD running. Download from futunn.com/openAPI."}</p>
            </div>
          </div>
        </div>
      </form>

      <div className="space-y-2">
        <h2 className="text-lg font-semibold tracking-tight">{"Email Notifications"}</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">{"Configure outbound SMTP to receive trade-action alerts, mandate events, and report emails. Leave the host blank to disable."}</p>
      </div>

      <form onSubmit={submitEmail} className="rounded-lg border bg-card p-5 shadow-sm">
        <div className="mb-5 space-y-1">
          <div className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">{"SMTP Settings"}</h2>
            <span className={`ml-auto rounded-full px-2 py-0.5 text-xs ${emailSettings.configured ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}>
              {emailSettings.configured ? "Configured" : "Not configured"}
            </span>
          </div>
        </div>

        <div className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,0.6fr)]">
            <label className="grid gap-2">
              <span className={labelClass}>{"SMTP host"}</span>
              <input
                value={smtpHost}
                onChange={(event) => setSmtpHost(event.target.value)}
                className={fieldClass}
                placeholder={"smtp.qq.com"}
              />
            </label>
            <label className="grid gap-2">
              <span className={labelClass}>{"Port"}</span>
              <input
                type="number"
                min={1}
                max={65535}
                value={smtpPort}
                onChange={(event) => setSmtpPort(event.target.value)}
                className={fieldClass}
                placeholder={"465"}
              />
            </label>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="grid gap-2">
              <span className={labelClass}>{"Username"}</span>
              <input
                value={smtpUser}
                onChange={(event) => setSmtpUser(event.target.value)}
                className={fieldClass}
                placeholder={"your-email@qq.com"}
                autoComplete="username"
              />
            </label>
            <label className="grid gap-2">
              <span className={labelClass}>{"Password"}</span>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="password"
                  value={smtpPassword}
                  onChange={(event) => setSmtpPassword(event.target.value)}
                  className={`${fieldClass} pl-9`}
                  placeholder={emailSettings.password_configured ? "Configured (leave blank to keep)" : "SMTP authorization code / password"}
                  autoComplete="current-password"
                  disabled={clearSmtpPassword}
                />
              </div>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={clearSmtpPassword}
                  onChange={(event) => {
                    setClearSmtpPassword(event.target.checked);
                    if (event.target.checked) setSmtpPassword("");
                  }}
                  className="h-3.5 w-3.5 accent-primary"
                />
                {"Clear saved password"}
              </label>
            </label>
          </div>

          <label className="grid gap-2">
            <span className={labelClass}>{"From address"}</span>
            <input
              value={smtpFrom}
              onChange={(event) => setSmtpFrom(event.target.value)}
              className={fieldClass}
              placeholder={"Defaults to the username above"}
            />
          </label>

          <label className="grid gap-2">
            <span className={labelClass}>{"Alert recipients"}</span>
            <input
              value={smtpRecipients}
              onChange={(event) => setSmtpRecipients(event.target.value)}
              className={fieldClass}
              placeholder={"alert@example.com, ops@example.com"}
            />
            <span className={hintClass}>{"Comma- or semicolon-separated. Trade-action and mandate alerts are sent here."}</span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={smtpUseTls}
              onChange={(event) => setSmtpUseTls(event.target.checked)}
              className="h-3.5 w-3.5 accent-primary"
            />
            <span>Use TLS (port 465 = implicit TLS, ports 25/587 = STARTTLS)</span>
          </label>

          <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{"Stored in"}: </span>
            <span className="break-all font-mono">{emailSettings.stored_in}</span>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              disabled={emailSaving}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {emailSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {emailSaving ? "Saving..." : "Save email settings"}
            </button>
            <button
              type="button"
              onClick={testEmail}
              disabled={emailTesting || !emailSettings.configured}
              className="inline-flex items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-70"
              title={emailSettings.configured ? "Send a test email using the saved SMTP config" : "Save a valid SMTP configuration first"}
            >
              {emailTesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {emailTesting ? "Sending..." : "Send test email"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
