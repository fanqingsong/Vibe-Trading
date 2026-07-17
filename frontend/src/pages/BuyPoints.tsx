import { useState } from "react";
import { Crosshair } from "lucide-react";

type Universe = "csi300" | "sp500" | "custom";

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

const UNIVERSES: { id: Universe; label: string }[] = [
  { id: "csi300", label: "CSI 300" },
  { id: "sp500", label: "S&P 500" },
  { id: "custom", label: "Custom" },
];

export function BuyPoints() {
  const [universe, setUniverse] = useState<Universe>("csi300");
  const [codes, setCodes] = useState("600036.SH,601288.SH,601398.SH,000001.SZ,600519.SH");
  const [lookback, setLookback] = useState(60);
  const [maxPullback, setMaxPullback] = useState(15);
  const [tolerancePct, setTolerancePct] = useState(2);
  const [freshness, setFreshness] = useState(10);
  const [requireVolume, setRequireVolume] = useState(true);
  const [top, setTop] = useState(50);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ScreenResult | null>(null);

  const runScreen = async () => {
    setError(null);
    setLoading(true);
    try {
      const params = new URLSearchParams({
        universe,
        prior_high_lookback: String(lookback),
        max_pullback_days: String(maxPullback),
        hold_tolerance: String(tolerancePct / 100),
        signal_freshness: String(freshness),
        require_volume: String(requireVolume),
        top: String(top),
      });
      if (universe === "custom") {
        params.set("codes", codes);
      }

      const result = await request<ScreenResult>(`/buy-points?${params.toString()}`);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Buy-point screen failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-6 p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <Crosshair className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">Right-Side Buy Points</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Breakout of prior high, pullback holds, then reclaim — volume confirm
            on by default. CSI 300 first run may take ~2 minutes (Tushare by-date
            bulk); later runs reuse a short cache.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-4 border rounded-lg p-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium">Universe</label>
          <div className="flex flex-wrap gap-1.5">
            {UNIVERSES.map((u) => (
              <button
                key={u.id}
                type="button"
                onClick={() => setUniverse(u.id)}
                className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                  universe === u.id
                    ? "bg-primary text-primary-foreground"
                    : "border-muted-foreground/30 hover:border-primary"
                }`}
              >
                {u.label}
              </button>
            ))}
          </div>
        </div>

        {universe === "custom" && (
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium">Tickers</label>
            <input
              type="text"
              value={codes}
              onChange={(e) => setCodes(e.target.value)}
              placeholder="600036.SH,601288.SH,AAPL,MSFT"
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated. A-shares need .SH/.SZ; US tickers are bare symbols.
            </p>
          </div>
        )}

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
                  {data.results.map((row, i) => (
                    <tr key={row.code} className="border-b last:border-0 hover:bg-muted/20">
                      <td className="px-3 py-2 text-muted-foreground">{i + 1}</td>
                      <td className="px-3 py-2 font-mono">{row.code}</td>
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
                  ))}
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
