import { Fragment, useEffect, useState } from "react";
import { ChevronDown, Crosshair, Mail } from "lucide-react";
import { Sparkline } from "@/components/charts/Sparkline";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { api, type PriceBar, type TradeMarker } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

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
  const authUser = useAuthStore((s) => s.user);
  const [lookback, setLookback] = useState(60);
  const [maxPullback, setMaxPullback] = useState(15);
  const [tolerancePct, setTolerancePct] = useState(2);
  const [freshness, setFreshness] = useState(10);
  const [requireVolume, setRequireVolume] = useState(true);
  const [top, setTop] = useState(50);
  const [loading, setLoading] = useState(false);
  const [emailing, setEmailing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailStatus, setEmailStatus] = useState<string | null>(null);
  const [data, setData] = useState<ScreenResult | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [logicOpen, setLogicOpen] = useState(true);

  const runScreen = async () => {
    setError(null);
    setEmailStatus(null);
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

  const sendEmail = async () => {
    if (!data || data.results.length === 0) return;
    setEmailStatus(null);
    setError(null);
    setEmailing(true);
    try {
      const result = await api.emailBuyPoints({
        universe: data.universe,
        market: data.market,
        trade_date: data.trade_date,
        prior_high_lookback: data.prior_high_lookback,
        prior_high_exclude: data.prior_high_exclude,
        min_pullback_days: data.min_pullback_days,
        max_pullback_days: data.max_pullback_days,
        hold_tolerance: data.hold_tolerance,
        signal_freshness: data.signal_freshness,
        require_volume: data.require_volume,
        volume_mult: data.volume_mult,
        universe_size: data.universe_size,
        fetched: data.fetched,
        matched: data.matched,
        count: data.count,
        source: data.source,
        results: data.results.map((row) => ({
          code: row.code,
          name: row.name,
          signal_date: row.signal_date,
          breakout_date: row.breakout_date,
          prior_high: row.prior_high,
          pullback_low: row.pullback_low,
          breakout_close: row.breakout_close,
          close: row.close,
          breakout_pct: row.breakout_pct,
          volume_ratio: row.volume_ratio,
          days_since_signal: row.days_since_signal,
          days_after_breakout: row.days_after_breakout,
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
        <Crosshair className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">Right-Side Buy Points</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            CSI 300 screen: breakout of prior high → pullback that holds → close
            reclaim of prior high. Click a row to expand the 120-day daily chart.
            First run may take ~2 minutes; later runs reuse a short-lived cache.
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
              Pattern: breakout → pullback holds (within tolerance) → close back
              above prior high
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
                <span className="text-foreground font-medium">Prior high</span>
                : skip the{" "}
                <span className="text-foreground tabular-nums">{PRIOR_HIGH_EXCLUDE}</span>{" "}
                sessions immediately before the breakout bar, then take the max
                high over the previous{" "}
                <span className="text-foreground tabular-nums">{lookback}</span>{" "}
                sessions (Prior-high lookback). Skipping recent bars keeps
                near-breakout highs out of the baseline.
              </li>
              <li>
                <span className="text-foreground font-medium">Breakout</span>
                : a session whose close is above that prior high is the
                breakout day. Optionally require breakout volume ≥ 20-day average
                ×{" "}
                <span className="text-foreground tabular-nums">1.2</span>
                {" "}
                (Require volume confirm — currently{" "}
                {requireVolume ? "on" : "off"}).
              </li>
              <li>
                <span className="text-foreground font-medium">Pullback holds</span>
                : within{" "}
                <span className="text-foreground tabular-nums">{MIN_PULLBACK_DAYS}</span>
                –{" "}
                <span className="text-foreground tabular-nums">{maxPullback}</span>{" "}
                sessions after breakout (Max pullback days), price must pull
                back: the low dips below the breakout close, but never below
                prior high × (1 −{" "}
                <span className="text-foreground tabular-nums">{tolerancePct}</span>
                %) (Hold tolerance). Breaking that floor fails the pattern.
              </li>
              <li>
                <span className="text-foreground font-medium">
                  Right-side confirm (buy point)
                </span>
                : after the pullback trough, the first session that closes back
                ≥ prior high is the signal day. The prior session must have
                probed near the prior high (close still below it, or low
                touching near it), so a straight run-up without a pullback is
                not counted.
              </li>
              <li>
                <span className="text-foreground font-medium">Freshness</span>
                : keep only signals whose signal day falls in the last{" "}
                <span className="text-foreground tabular-nums">{freshness}</span>{" "}
                sessions (Signal freshness). Per name, keep the newest signal
                (tie-break: larger breakout %), then sort by signal date and
                take Top{" "}
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
                    — mini close path (~60 sessions); dashed line marks prior
                    high; thicker point marks the signal day.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Signal</dt>
                  <dd className="inline">
                    {" "}
                    — right-side confirm date (close reclaim of prior high);
                    this is the buy-point day.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Breakout</dt>
                  <dd className="inline">
                    {" "}
                    — date when close first cleared the prior high.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Prior High</dt>
                  <dd className="inline">
                    {" "}
                    — reference high used for breakout / reclaim.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Pullback Low</dt>
                  <dd className="inline">
                    {" "}
                    — lowest low between breakout and signal; must stay above
                    the hold-tolerance floor.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Close</dt>
                  <dd className="inline">
                    {" "}
                    — closing price on the signal day.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Breakout %</dt>
                  <dd className="inline">
                    {" "}
                    — (breakout close − prior high) / prior high × 100.
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Vol Ratio</dt>
                  <dd className="inline">
                    {" "}
                    — breakout volume ÷ 20-day average volume (— if unavailable).
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Days Ago</dt>
                  <dd className="inline">
                    {" "}
                    — trading sessions since the signal day (0 = today / latest
                    bar).
                  </dd>
                </div>
                <div>
                  <dt className="inline font-medium text-foreground">Expanded chart</dt>
                  <dd className="inline">
                    {" "}
                    — 120-day daily candles with Breakout / Buy markers.
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
            <p className="text-[11px] text-muted-foreground leading-snug">
              Sessions used to measure the prior high (after skipping{" "}
              {PRIOR_HIGH_EXCLUDE} bars before breakout).
            </p>
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
            <p className="text-[11px] text-muted-foreground leading-snug">
              Signal must arrive within {MIN_PULLBACK_DAYS}–this many sessions
              after breakout.
            </p>
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
            <p className="text-[11px] text-muted-foreground leading-snug">
              Max % the pullback low may fall below the prior high.
            </p>
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
            <p className="text-[11px] text-muted-foreground leading-snug">
              Only include signals from the last N trading sessions.
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

      {emailStatus && (
        <div className="text-sm text-foreground border border-border rounded p-3 bg-muted/30">
          {emailStatus}
        </div>
      )}

      {loading && !data && !error && (
        <div className="text-sm text-muted-foreground border rounded-lg p-6 text-center">
          Screening CSI 300… first run may take ~2 minutes.
        </div>
      )}

      {data && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
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
