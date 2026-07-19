import { Fragment, useEffect, useState } from "react";
import { ChevronDown, Crosshair } from "lucide-react";
import { Sparkline } from "@/components/charts/Sparkline";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import type { PriceBar, TradeMarker } from "@/lib/api";

/** Fixed backend default (not exposed in the form). */
const PRIOR_HIGH_EXCLUDE = 5;
const MIN_PULLBACK_DAYS = 3;

const UNIVERSE = "csi300";

interface SparkPoint {
  date: string;
  close: number;
}

interface BuyPointRow {
  code: string;
  name: string;
  signal_date: string;
  breakout_date: string;
  prior_high: number;
  pullback_low: number;
  breakout_close: number;
  close: number;
  breakout_pct: number;
  volume_ratio: number | null;
  days_since_signal: number;
  days_after_breakout: number;
  sparkline?: SparkPoint[];
  bars?: PriceBar[];
}

interface ScreenResult {
  universe: string;
  market: string;
  trade_date: string;
  prior_high_lookback: number;
  prior_high_exclude: number;
  min_pullback_days: number;
  max_pullback_days: number;
  hold_tolerance: number;
  signal_freshness: number;
  require_volume: boolean;
  volume_mult: number;
  universe_size: number;
  fetched: number;
  matched: number;
  count: number;
  source: string;
  warning?: string | null;
  results: BuyPointRow[];
}

export function BuyPoints() {
  const [lookback, setLookback] = useState(60);
  const [maxPullback, setMaxPullback] = useState(15);
  const [tolerancePct, setTolerancePct] = useState(2);
  const [freshness, setFreshness] = useState(10);
  const [requireVolume, setRequireVolume] = useState(true);
  const [top, setTop] = useState(50);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ScreenResult | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [logicOpen, setLogicOpen] = useState(true);

  const runScreen = async () => {
    setError(null);
    setLoading(true);
    setExpandedCode(null);
    try {
      const params = new URLSearchParams({
        universe: UNIVERSE,
        prior_high_lookback: String(lookback),
        max_pullback_days: String(maxPullback),
        hold_tolerance: String(tolerancePct / 100),
        signal_freshness: String(freshness),
        require_volume: String(requireVolume),
        top: String(top),
      });

      const result = await request<ScreenResult>(`/buy-points?${params.toString()}`);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Buy-point screen failed");
      setData(null);
    } finally {
      setLoading(false);
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
        <Crosshair className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">Right-Side Buy Points</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            CSI 300 右侧买点：突破前高 → 回踩站稳 → 重新站上前高。点击行可展开
            120 日 K 线。首次筛选约需 2 分钟，之后会复用短时缓存。
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
            <div className="text-sm font-medium">筛选逻辑说明</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              形态：突破前高 → 回踩不破（容差内）→ 收盘重新站上前高
            </div>
          </div>
          <ChevronDown
            className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
              logicOpen ? "rotate-180" : ""
            }`}
          />
        </button>

        {logicOpen && (
          <div className="border-t px-4 py-4 flex flex-col gap-4 text-sm">
            <ol className="list-decimal list-outside pl-5 space-y-2.5 text-muted-foreground">
              <li>
                <span className="text-foreground font-medium">定义前高</span>
                ：取突破日之前再往前跳过{" "}
                <span className="text-foreground tabular-nums">{PRIOR_HIGH_EXCLUDE}</span>{" "}
                个交易日，再往前看{" "}
                <span className="text-foreground tabular-nums">{lookback}</span>{" "}
                日（Prior-high lookback）的最高价作为前高。跳过最近几日是为了避免把即将突破的高点算进基准。
              </li>
              <li>
                <span className="text-foreground font-medium">突破</span>
                ：某日收盘价首次有效站上该前高，记为突破日；可选要求当日成交量 ≥ 近 20 日均量 ×{" "}
                <span className="text-foreground tabular-nums">1.2</span>
                （Require volume confirm
                {requireVolume ? "，当前开启" : "，当前关闭"}）。
              </li>
              <li>
                <span className="text-foreground font-medium">回踩站稳</span>
                ：突破后第{" "}
                <span className="text-foreground tabular-nums">{MIN_PULLBACK_DAYS}</span>
                –{" "}
                <span className="text-foreground tabular-nums">{maxPullback}</span>{" "}
                个交易日内（Max pullback days）出现回踩：最低价低于突破日收盘，但始终不低于前高 × (1 −{" "}
                <span className="text-foreground tabular-nums">{tolerancePct}</span>
                %)（Hold tolerance），即跌破容差则形态失败。
              </li>
              <li>
                <span className="text-foreground font-medium">右侧确认（买点）</span>
                ：回踩低点之后，首个收盘重新 ≥ 前高的交易日记为信号日；要求前一日曾试探前高附近（收盘仍低于前高，或最低价触及前高附近），避免把从未回踩的强势续涨误判为买点。
              </li>
              <li>
                <span className="text-foreground font-medium">新鲜度</span>
                ：只保留信号日落在最近{" "}
                <span className="text-foreground tabular-nums">{freshness}</span>{" "}
                个交易日内的结果（Signal freshness）；每只股票取最新且突破幅度更大的一条，再按信号日排序取 Top{" "}
                <span className="text-foreground tabular-nums">{top}</span>。
              </li>
            </ol>

            <div className="rounded-md bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground leading-relaxed">
              <span className="text-foreground font-medium">读表提示：</span>
              Signal = 右侧确认日；Breakout = 突破日；Prior High / Pullback Low =
              前高与回踩最低；Breakout % = 突破日相对前高的涨幅；Vol Ratio =
              突破日量能相对 20 日均量；Days Ago = 信号距今交易日数。展开行可看带
              Breakout / Buy 标记的 120 日 K 线。
            </div>
          </div>
        )}
      </section>

      <div className="flex flex-col gap-4 border rounded-lg p-4">
        <div className="text-sm text-muted-foreground">
          Universe: <span className="font-medium text-foreground">CSI 300</span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Prior-high lookback</label>
            <input
              type="number"
              min={10}
              max={250}
              value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Max pullback days</label>
            <input
              type="number"
              min={3}
              max={60}
              value={maxPullback}
              onChange={(e) => setMaxPullback(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Hold tolerance (%)</label>
            <input
              type="number"
              min={0}
              max={20}
              step={0.5}
              value={tolerancePct}
              onChange={(e) => setTolerancePct(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Signal freshness</label>
            <input
              type="number"
              min={1}
              max={30}
              value={freshness}
              onChange={(e) => setFreshness(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
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
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <label className="flex items-center gap-2 text-sm cursor-pointer select-none pb-2">
            <input
              type="checkbox"
              checked={requireVolume}
              onChange={(e) => setRequireVolume(e.target.checked)}
              className="rounded border"
            />
            Require volume confirm (breakout ≥ 1.2× 20-day avg)
          </label>
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

      {loading && !data && !error && (
        <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
          Screening CSI 300… first run may take ~2 minutes.
        </div>
      )}

      {data && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              Date {data.trade_date || "—"} · {data.matched}/{data.fetched} matched
              (universe {data.universe_size}) · showing {data.count}
            </span>
            <span>Source: {data.source}</span>
            <span>
              Volume {data.require_volume ? `on ×${data.volume_mult}` : "off"}
            </span>
          </div>

          {data.warning && (
            <div className="text-sm text-amber-700 dark:text-amber-400 border border-amber-500/30 rounded p-3 bg-amber-500/5">
              {data.warning}
            </div>
          )}

          {data.results.length === 0 ? (
            <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
              No names passed the current filters. Try unchecking volume confirm,
              raising signal freshness, or wait ~1 minute if Tushare rate-limited
              the previous CSI 300 pull.
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
                    <th className="px-3 py-2 font-medium">Signal</th>
                    <th className="px-3 py-2 font-medium">Breakout</th>
                    <th className="px-3 py-2 font-medium text-right">Prior High</th>
                    <th className="px-3 py-2 font-medium text-right">Pullback Low</th>
                    <th className="px-3 py-2 font-medium text-right">Close</th>
                    <th className="px-3 py-2 font-medium text-right">Breakout %</th>
                    <th className="px-3 py-2 font-medium text-right">Vol Ratio</th>
                    <th className="px-3 py-2 font-medium text-right">Days Ago</th>
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
                              priorHigh={row.prior_high}
                              signalDate={row.signal_date}
                              width={128}
                              height={36}
                              className="text-muted-foreground"
                            />
                          </td>
                          <td className="px-3 py-2 tabular-nums">{row.signal_date}</td>
                          <td className="px-3 py-2 tabular-nums">{row.breakout_date}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmt(row.prior_high)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmt(row.pullback_low)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmt(row.close)}</td>
                          <td className="px-3 py-2 text-right font-medium tabular-nums">
                            {fmt(row.breakout_pct)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {row.volume_ratio == null ? "—" : row.volume_ratio.toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {row.days_since_signal}
                          </td>
                        </tr>
                        {open && (
                          <tr className="border-b bg-muted/10">
                            <td colSpan={12} className="px-3 py-4">
                              <div className="flex flex-col gap-2">
                                <div className="text-xs text-muted-foreground">
                                  {row.name || row.code} · 120-day daily chart ·
                                  markers: breakout / right-side buy
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

function chartMarkers(row: BuyPointRow): TradeMarker[] {
  const markers: TradeMarker[] = [];
  const breakoutBar = row.bars?.find((b) => b.time === row.breakout_date);
  const signalBar = row.bars?.find((b) => b.time === row.signal_date);
  if (breakoutBar) {
    markers.push({
      time: row.breakout_date,
      code: row.code,
      side: "BUY",
      price: breakoutBar.close,
      reason: "Breakout",
      text: "Breakout",
    });
  }
  if (signalBar) {
    markers.push({
      time: row.signal_date,
      code: row.code,
      side: "BUY",
      price: signalBar.close,
      reason: "Right-side buy",
      text: "Buy",
    });
  }
  return markers;
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
