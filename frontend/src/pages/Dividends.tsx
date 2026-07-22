import { Fragment, useEffect, useState } from "react";
import { ChevronDown, Mail, Percent } from "lucide-react";
import { Sparkline } from "@/components/charts/Sparkline";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { api, type PriceBar } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

const UNIVERSE = "csi300";

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
  const [minYield, setMinYield] = useState(3);
  const [maxYield, setMaxYield] = useState<string>("");
  const [minMv, setMinMv] = useState<string>("");
  const [maxPe, setMaxPe] = useState<string>("");
  const [top, setTop] = useState(50);
  const [loading, setLoading] = useState(false);
  const [emailing, setEmailing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailStatus, setEmailStatus] = useState<string | null>(null);
  const [data, setData] = useState<ScreenResult | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);

  const runScreen = async () => {
    setError(null);
    setEmailStatus(null);
    setLoading(true);
    setExpandedCode(null);
    try {
      const params = new URLSearchParams({
        universe: UNIVERSE,
        min_yield: String(minYield),
        top: String(top),
      });
      if (maxYield.trim()) params.set("max_yield", maxYield.trim());
      if (minMv.trim()) params.set("min_market_cap", minMv.trim());
      if (maxPe.trim()) params.set("max_pe", maxPe.trim());

      const result = await request<ScreenResult>(`/dividends?${params.toString()}`);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Dividend screen failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  const sendEmail = async () => {
    if (!data || data.results.length === 0) return;
    setEmailStatus(null);
    setError(null);
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
        setError(result.message || "Failed to send email");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send email");
    } finally {
      setEmailing(false);
    }
  };

  useEffect(() => {
    void runScreen();
    // Auto-load once on enter with default filter values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

      {loading && !data && !error && (
        <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
          Screening CSI 300…
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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const text = await res.text();
  return text ? JSON.parse(text) : ({} as T);
}
