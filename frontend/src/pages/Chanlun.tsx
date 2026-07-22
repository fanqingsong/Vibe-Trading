import { Fragment, useEffect, useState } from "react";
import { ChevronDown, GitBranch, Mail } from "lucide-react";
import { Sparkline } from "@/components/charts/Sparkline";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { api, type PriceBar, type TradeMarker } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

const UNIVERSE = "csi300";

type BuyType = "buy1" | "buy2" | "buy3";

const BUY_OPTIONS: { value: BuyType; label: string; blurb: string }[] = [
  {
    value: "buy3",
    label: "三买",
    blurb: "突破中枢 ZG 后回调不进中枢（最接近右侧买点）",
  },
  {
    value: "buy2",
    label: "二买",
    blurb: "一买后反弹再回调，回调不破前低",
  },
  {
    value: "buy1",
    label: "一买",
    blurb: "下跌趋势末段背驰 + 底分型",
  },
];

interface SparkPoint {
  date: string;
  close: number;
}

interface ChanlunRow {
  code: string;
  name: string;
  signal_date: string;
  buy_type: BuyType;
  buy_label: string;
  signal_detail: string;
  close: number;
  zg: number | null;
  zd: number | null;
  bi_high: number | null;
  bi_low: number | null;
  days_since_signal: number;
  sparkline?: SparkPoint[];
  bars?: PriceBar[];
}

interface ScreenResult {
  universe: string;
  market: string;
  trade_date: string;
  buy_type: BuyType;
  buy_label: string;
  signal_freshness: number;
  ma_period: number;
  universe_size: number;
  fetched: number;
  matched: number;
  count: number;
  source: string;
  warning?: string | null;
  results: ChanlunRow[];
}

export function Chanlun() {
  const authUser = useAuthStore((s) => s.user);
  const [buyType, setBuyType] = useState<BuyType>("buy3");
  const [freshness, setFreshness] = useState(10);
  const [maPeriod, setMaPeriod] = useState(34);
  const [top, setTop] = useState(50);
  const [loading, setLoading] = useState(false);
  const [emailing, setEmailing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailStatus, setEmailStatus] = useState<string | null>(null);
  const [data, setData] = useState<ScreenResult | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [logicOpen, setLogicOpen] = useState(true);
  const [elapsedSec, setElapsedSec] = useState(0);

  const runScreen = async () => {
    setError(null);
    setEmailStatus(null);
    setLoading(true);
    setExpandedCode(null);
    setElapsedSec(0);
    const started = Date.now();
    const tick = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    try {
      const params = new URLSearchParams({
        universe: UNIVERSE,
        buy_type: buyType,
        signal_freshness: String(freshness),
        ma_period: String(maPeriod),
        top: String(top),
      });

      const result = await request<ScreenResult>(`/chanlun?${params.toString()}`);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chanlun screen failed");
      setData(null);
    } finally {
      window.clearInterval(tick);
      setLoading(false);
    }
  };

  const sendEmail = async () => {
    if (!data || data.results.length === 0) return;
    setEmailStatus(null);
    setError(null);
    setEmailing(true);
    try {
      const result = await api.emailChanlun({
        universe: data.universe,
        market: data.market,
        trade_date: data.trade_date,
        buy_type: data.buy_type,
        buy_label: data.buy_label,
        signal_freshness: data.signal_freshness,
        ma_period: data.ma_period,
        universe_size: data.universe_size,
        fetched: data.fetched,
        matched: data.matched,
        count: data.count,
        source: data.source,
        results: data.results.map((row) => ({
          code: row.code,
          name: row.name,
          signal_date: row.signal_date,
          buy_type: row.buy_type,
          buy_label: row.buy_label,
          signal_detail: row.signal_detail,
          close: row.close,
          zg: row.zg,
          zd: row.zd,
          bi_high: row.bi_high,
          bi_low: row.bi_low,
          days_since_signal: row.days_since_signal,
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

  const selected = BUY_OPTIONS.find((o) => o.value === buyType)!;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <GitBranch className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">Chanlun Buy Points</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            CSI 300 screen via czsc: fractal → bi → zhongshu → 一买 / 二买 / 三买.
            Click a row to expand the 120-day daily chart. First run may take a few
            minutes; later runs reuse the short-lived OHLCV cache.
          </p>
        </div>
      </div>

      <section className="border rounded-lg overflow-hidden">
        <button
          type="button"
          onClick={() => setLogicOpen((v) => !v)}
          className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-muted/30 transition-colors"
        >
          <div>
            <div className="text-sm font-medium">Screening logic</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              Selected: {selected.label} — {selected.blurb}
            </div>
          </div>
          <ChevronDown
            className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
              logicOpen ? "rotate-180" : ""
            }`}
          />
        </button>

        {logicOpen && (
          <div className="border-t px-4 py-4 flex flex-col gap-5 text-sm">
            <ol className="list-decimal list-outside pl-5 space-y-2.5 text-muted-foreground">
              <li>
                <span className="text-foreground font-medium">Structure</span>
                : daily bars are processed by{" "}
                <span className="text-foreground">czsc</span> — inclusion
                handling → fractals → bi → zhongshu (ZG / ZD).
              </li>
              <li>
                <span className="text-foreground font-medium">一买</span>
                : downtrend with ≥2 pivots, final decline shows divergence
                (背驰), confirmed by a bottom fractal (
                <code className="text-xs">cxt_first_buy_V221126</code>).
              </li>
              <li>
                <span className="text-foreground font-medium">二买</span>
                : after a first-buy style bounce, pullback that does not break
                the prior low, with SMA assist (
                <code className="text-xs">cxt_second_bs_V230320</code>).
              </li>
              <li>
                <span className="text-foreground font-medium">三买</span>
                : leave the zhongshu above ZG, pullback that stays above ZG
                (does not re-enter), with SMA assist (
                <code className="text-xs">cxt_third_bs_V230319</code> +{" "}
                <code className="text-xs">cxt_third_buy_V230228</code>). Closest
                to the right-side buy-point idea.
              </li>
              <li>
                <span className="text-foreground font-medium">Freshness</span>
                : keep only the latest{" "}
                <span className="text-foreground font-medium">onset</span> of
                the selected buy type within the last{" "}
                <span className="text-foreground tabular-nums">{freshness}</span>{" "}
                sessions, then take Top{" "}
                <span className="text-foreground tabular-nums">{top}</span>.
              </li>
            </ol>

            <div className="flex flex-col gap-2">
              <div className="text-sm font-medium text-foreground">
                Result table columns
              </div>
              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs text-muted-foreground">
                <div>
                  <dt className="inline font-medium text-foreground">Code</dt>
                  <dd className="inline"> — ticker / exchange symbol.</dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Name</dt>
                  <dd className="inline"> — company short name.</dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Trend (60d)</dt>
                  <dd className="inline">
                    {" "}
                    — mini close path; dashed line marks ZG when available;
                    thicker point marks the signal day.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Type</dt>
                  <dd className="inline"> — 一买 / 二买 / 三买.</dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Signal</dt>
                  <dd className="inline">
                    {" "}
                    — onset date when the czsc buy label first fired.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">ZG / ZD</dt>
                  <dd className="inline">
                    {" "}
                    — latest valid zhongshu high / low (— if none).
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Close</dt>
                  <dd className="inline"> — closing price on the signal day.</dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Days Ago</dt>
                  <dd className="inline">
                    {" "}
                    — trading sessions since the signal day.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Detail</dt>
                  <dd className="inline">
                    {" "}
                    — raw czsc signal string (e.g. 三买_均线新高).
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Expanded chart</dt>
                  <dd className="inline">
                    {" "}
                    — 120-day daily candles with a Buy marker on the signal day.
                  </dd>
                </div>
              </dl>
            </div>
          </div>
        )}
      </section>

      <div className="flex flex-col gap-4 border rounded-lg p-4">
        <div className="text-sm text-muted-foreground">
          Universe: <span className="font-medium text-foreground">CSI 300</span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Buy type</label>
            <select
              value={buyType}
              onChange={(e) => setBuyType(e.target.value as BuyType)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            >
              {BUY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground leading-snug">
              {selected.blurb}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Signal freshness</label>
            <input
              type="number"
              min={1}
              max={60}
              value={freshness}
              onChange={(e) => setFreshness(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-[11px] text-muted-foreground leading-snug">
              Only include buy onsets from the last N trading sessions.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">SMA period</label>
            <input
              type="number"
              min={2}
              max={120}
              value={maPeriod}
              onChange={(e) => setMaPeriod(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-[11px] text-muted-foreground leading-snug">
              Used by czsc 二买 / 三买 SMA helpers (default 34 for 三买).
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Top N</label>
            <input
              type="number"
              min={1}
              max={500}
              value={top}
              onChange={(e) => setTop(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-[11px] text-muted-foreground leading-snug">
              Max rows to return after sorting by signal date.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
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

      {loading && (
        <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
          Screening CSI 300 for {selected.label}… {elapsedSec}s elapsed.
          {elapsedSec < 20
            ? " Fetching daily bars…"
            : " Running czsc on each name — usually finishes within ~1 minute."}
        </div>
      )}

      {data && !loading && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>
                Date {data.trade_date || "—"} · {data.matched}/{data.fetched} matched
                (universe {data.universe_size}) · showing {data.count}
              </span>
              <span>Source: {data.source}</span>
              <span>
                {data.buy_label} · freshness {data.signal_freshness} · SMA{" "}
                {data.ma_period}
              </span>
            </div>
            <button
              type="button"
              onClick={sendEmail}
              disabled={emailing || data.results.length === 0}
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

          {data.warning && (
            <div className="text-sm text-amber-700 dark:text-amber-400 border border-amber-500/30 rounded p-3 bg-amber-500/5">
              {data.warning}
            </div>
          )}

          {data.results.length === 0 ? (
            <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
              No names passed the current filters. Try another buy type, raise
              signal freshness, or wait if the previous CSI 300 pull was
              rate-limited.
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
                    <th className="px-3 py-2 font-medium">Type</th>
                    <th className="px-3 py-2 font-medium">Signal</th>
                    <th className="px-3 py-2 font-medium text-right">ZG</th>
                    <th className="px-3 py-2 font-medium text-right">ZD</th>
                    <th className="px-3 py-2 font-medium text-right">Close</th>
                    <th className="px-3 py-2 font-medium text-right">Days Ago</th>
                    <th className="px-3 py-2 font-medium">Detail</th>
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
                          onClick={() =>
                            setExpandedCode(open ? null : row.code)
                          }
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
                              priorHigh={row.zg ?? undefined}
                              signalDate={row.signal_date}
                              width={128}
                              height={36}
                              className="text-muted-foreground"
                            />
                          </td>
                          <td className="px-3 py-2">{row.buy_label}</td>
                          <td className="px-3 py-2 tabular-nums">{row.signal_date}</td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmt(row.zg)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmt(row.zd)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {fmt(row.close)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {row.days_since_signal}
                          </td>
                          <td
                            className="px-3 py-2 text-xs text-muted-foreground max-w-[12rem] truncate"
                            title={row.signal_detail}
                          >
                            {row.signal_detail || "—"}
                          </td>
                        </tr>
                        {open && (
                          <tr className="border-b bg-muted/10">
                            <td colSpan={11} className="px-3 py-4">
                              <div className="flex flex-col gap-2">
                                <div className="text-xs text-muted-foreground">
                                  {row.name || row.code} · 120-day daily chart ·
                                  marker: {row.buy_label}
                                  {row.zg != null ? ` · ZG ${fmt(row.zg)}` : ""}
                                  {row.zd != null ? ` · ZD ${fmt(row.zd)}` : ""}
                                </div>
                                {(row.bars?.length ?? 0) < 2 ? (
                                  <div className="text-sm text-muted-foreground py-8 text-center">
                                    No bar data available for this symbol.
                                  </div>
                                ) : (
                                  <CandlestickChart
                                    data={row.bars!}
                                    markers={chartMarkers(row)}
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

function fmt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}

function chartMarkers(row: ChanlunRow): TradeMarker[] {
  const signalBar = row.bars?.find((b) => b.time === row.signal_date);
  if (!signalBar) return [];
  return [
    {
      time: row.signal_date,
      code: row.code,
      side: "BUY",
      price: signalBar.close,
      reason: row.buy_label,
      text: row.buy_label,
    },
  ];
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
