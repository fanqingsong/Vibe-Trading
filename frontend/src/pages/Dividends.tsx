import { Fragment, useState } from "react";
import { ChevronDown, Mail, Percent } from "lucide-react";
import { Sparkline } from "@/components/charts/Sparkline";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { api, type PriceBar } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";
import {
  numScreenParam,
  strScreenParam,
  useScreenJob,
} from "@/hooks/useScreenJob";

const UNIVERSE = "csi300";
const STORAGE_KEY = "vibe:screen:dividends";

interface SparkPoint {
  date: string;
  close: number;
}

interface DividendRow {
  code: string;
  name: string;
  dividend_yield: number;
  pe: number | null;
  pb: number | null;
  market_cap: number | null;
  close: number | null;
  sparkline?: SparkPoint[];
  bars?: PriceBar[];
}

interface ScreenResult {
  universe: string;
  market: string;
  trade_date: string;
  min_yield: number;
  max_yield: number | null;
  market_cap_unit: string;
  universe_size: number;
  matched: number;
  count: number;
  source: string;
  results: DividendRow[];
}

export function Dividends() {
  const authUser = useAuthStore((s) => s.user);
  // Filter defaults rehydrate from the stored screen job (if any) so a page
  // refresh restores both the running screen and the form values behind it.
  const [minYield, setMinYield] = useState(() => numScreenParam(STORAGE_KEY, "min_yield", 3));
  const [maxYield, setMaxYield] = useState(() => strScreenParam(STORAGE_KEY, "max_yield", ""));
  const [minMv, setMinMv] = useState(() => strScreenParam(STORAGE_KEY, "min_market_cap", ""));
  const [maxPe, setMaxPe] = useState(() => strScreenParam(STORAGE_KEY, "max_pe", ""));
  const [top, setTop] = useState(() => numScreenParam(STORAGE_KEY, "top", 50));
  const [emailing, setEmailing] = useState(false);
  const [emailStatus, setEmailStatus] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);

  const buildParams = () => {
    const params: Record<string, unknown> = {
      universe: UNIVERSE,
      min_yield: minYield,
      top,
    };
    if (maxYield.trim()) params.max_yield = Number(maxYield.trim());
    if (minMv.trim()) params.min_market_cap = Number(minMv.trim());
    if (maxPe.trim()) params.max_pe = Number(maxPe.trim());
    return params;
  };

  // Background job screen: auto-loads on enter and survives page refreshes
  // (re-attaches to the running job instead of restarting the screen).
  const screen = useScreenJob<ScreenResult>({
    kind: "dividends",
    storageKey: STORAGE_KEY,
    buildParams,
  });
  const loading = screen.phase === "running";
  const error = screen.error ?? emailError;
  const data = screen.data;

  const runScreen = () => {
    setEmailStatus(null);
    setEmailError(null);
    setExpandedCode(null);
    screen.start();
  };

  const sendEmail = async () => {
    if (!data || data.results.length === 0) return;
    setEmailStatus(null);
    setEmailError(null);
    setEmailing(true);
    try {
      const result = await api.emailDividends({
        universe: data.universe,
        market: data.market,
        trade_date: data.trade_date,
        min_yield: data.min_yield,
        max_yield: data.max_yield,
        market_cap_unit: data.market_cap_unit,
        universe_size: data.universe_size,
        matched: data.matched,
        count: data.count,
        source: data.source,
        results: data.results.map((row) => ({
          code: row.code,
          name: row.name,
          dividend_yield: row.dividend_yield,
          pe: row.pe,
          pb: row.pb,
          market_cap: row.market_cap,
          close: row.close,
        })),
      });
      if (result.ok) {
        const to = result.recipients.join(", ") || authUser?.email || "your inbox";
        setEmailStatus(`Sent to ${to}`);
      } else {
        setEmailError(result.message || "Failed to send email");
      }
    } catch (e) {
      setEmailError(e instanceof Error ? e.message : "Failed to send email");
    } finally {
      setEmailing(false);
    }
  };

  return (
    <div className="flex flex-col gap-6 p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <Percent className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">High Dividend Screen</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Rank CSI 300 equities by trailing dividend yield (Tushare dv_ttm,
            AKShare 分红送配 as free fallback). Click a row to expand the
            120-day candlestick chart.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-4 border rounded-lg p-4">
        <div className="text-sm text-muted-foreground">
          Universe: <span className="font-medium text-foreground">CSI 300</span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Min yield (%)</label>
            <input
              type="number"
              min={0}
              step={0.1}
              value={minYield}
              onChange={(e) => setMinYield(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Max yield (%)</label>
            <input
              type="number"
              min={0}
              step={0.1}
              value={maxYield}
              onChange={(e) => setMaxYield(e.target.value)}
              placeholder="optional"
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Min market cap (亿元)</label>
            <input
              type="number"
              min={0}
              value={minMv}
              onChange={(e) => setMinMv(e.target.value)}
              placeholder="optional"
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Max PE</label>
            <input
              type="number"
              min={0}
              value={maxPe}
              onChange={(e) => setMaxPe(e.target.value)}
              placeholder="optional"
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Top N</label>
            <input
              type="number"
              min={1}
              max={500}
              value={top}
              onChange={(e) => setTop(Number(e.target.value))}
              className="w-28 px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <button
            type="button"
            onClick={runScreen}
            disabled={loading}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {loading ? "Screening…" : "Screen"}
          </button>
        </div>
      </div>

      {error && (
        <div className="text-sm text-danger border border-danger/30 rounded p-3 bg-danger/5">
          {error}
        </div>
      )}

      {emailStatus && (
        <div className="text-sm text-foreground border border-border rounded p-3 bg-muted/30">
          {emailStatus}
        </div>
      )}

      {loading && !error && (
        <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
          Screening CSI 300… {screen.elapsedSec}s elapsed.
          {screen.resumed && " Re-attached to the background screen after refresh."}
        </div>
      )}

      {data && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>
                Date {data.trade_date} · {data.matched}/{data.universe_size} matched · showing{" "}
                {data.count}
              </span>
              <span>Source: {data.source}</span>
              <span>Cap unit: 亿元</span>
            </div>
            <button
              type="button"
              onClick={sendEmail}
              disabled={emailing || loading || data.results.length === 0}
              title={
                authUser?.email
                  ? `Send results to ${authUser.email}`
                  : "Send results to your account email"
              }
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border bg-background text-sm font-medium hover:bg-muted/50 disabled:opacity-50 transition-colors"
            >
              <Mail className="h-3.5 w-3.5" />
              {emailing ? "Sending…" : "Email results"}
            </button>
          </div>

          {data.results.length === 0 ? (
            <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
              No names passed the current filters.
            </div>
          ) : (
            <div className="border rounded-lg overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/40 text-left">
                    <th className="px-3 py-2 font-medium w-10">#</th>
                    <th className="px-3 py-2 font-medium">Code</th>
                    <th className="px-3 py-2 font-medium">Name</th>
                    <th className="px-3 py-2 font-medium">Trend (60d)</th>
                    <th className="px-3 py-2 font-medium text-right">Yield %</th>
                    <th className="px-3 py-2 font-medium text-right">PE</th>
                    <th className="px-3 py-2 font-medium text-right">PB</th>
                    <th className="px-3 py-2 font-medium text-right">Mkt Cap (亿元)</th>
                    <th className="px-3 py-2 font-medium text-right">Close</th>
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((row, i) => {
                    const open = expandedCode === row.code;
                    return (
                      <Fragment key={row.code}>
                        <tr
                          className={`border-b last:border-0 hover:bg-muted/20 cursor-pointer ${
                            open ? "bg-muted/30" : ""
                          }`}
                          onClick={() => setExpandedCode(open ? null : row.code)}
                        >
                          <td className="px-3 py-2 text-muted-foreground">
                            <span className="inline-flex items-center gap-1">
                              <ChevronDown
                                className={`h-3.5 w-3.5 transition-transform ${
                                  open ? "rotate-180" : ""
                                }`}
                              />
                              {i + 1}
                            </span>
                          </td>
                          <td className="px-3 py-2 font-mono">{row.code}</td>
                          <td className="px-3 py-2">{row.name || "—"}</td>
                          <td className="px-3 py-2">
                            <Sparkline
                              points={row.sparkline ?? []}
                              width={128}
                              height={36}
                              className="text-muted-foreground"
                            />
                          </td>
                          <td className="px-3 py-2 text-right font-medium tabular-nums">
                            {row.dividend_yield.toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmt(row.pe)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmt(row.pb)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmtCap(row.market_cap)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmt(row.close)}
                          </td>
                        </tr>
                        {open && (
                          <tr className="border-b bg-muted/10">
                            <td colSpan={9} className="px-3 py-4">
                              <div className="flex flex-col gap-2">
                                <div className="text-xs text-muted-foreground">
                                  {row.name || row.code} · yield{" "}
                                  {row.dividend_yield.toFixed(2)}% · 120-day daily
                                  chart
                                </div>
                                {(row.bars?.length ?? 0) < 2 ? (
                                  <div className="text-sm text-muted-foreground py-8 text-center">
                                    No bar data available for this symbol.
                                  </div>
                                ) : (
                                  <CandlestickChart
                                    data={row.bars!}
                                    height={420}
                                  />
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function fmt(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}

function fmtCap(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(1);
}
