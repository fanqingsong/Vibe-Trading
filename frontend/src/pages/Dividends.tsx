import { useState } from "react";
import { Percent } from "lucide-react";

type Universe = "csi300" | "sp500" | "custom";

interface DividendRow {
  code: string;
  name: string;
  dividend_yield: number;
  pe: number | null;
  pb: number | null;
  market_cap: number | null;
  close: number | null;
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

const UNIVERSES: { id: Universe; label: string }[] = [
  { id: "csi300", label: "CSI 300" },
  { id: "sp500", label: "S&P 500" },
  { id: "custom", label: "Custom" },
];

export function Dividends() {
  const [universe, setUniverse] = useState<Universe>("csi300");
  const [codes, setCodes] = useState("600036.SH,601288.SH,601398.SH,601988.SH,600028.SH");
  const [minYield, setMinYield] = useState(3);
  const [maxYield, setMaxYield] = useState<string>("");
  const [minMv, setMinMv] = useState<string>("");
  const [maxPe, setMaxPe] = useState<string>("");
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
        min_yield: String(minYield),
        top: String(top),
      });
      if (universe === "custom") {
        params.set("codes", codes);
      }
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

  const mvLabel =
    universe === "sp500"
      ? "Min market cap (USD)"
      : universe === "csi300"
        ? "Min market cap (亿元)"
        : "Min market cap (亿元 / USD)";

  return (
    <div className="flex flex-col gap-6 p-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <Percent className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-2xl font-bold">High Dividend Screen</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Rank equities by trailing dividend yield. A-shares use Tushare dv_ttm; US uses yfinance.
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
              placeholder="600036.SH,601288.SH,XOM,JNJ"
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated. A-shares need .SH/.SZ; US tickers are bare symbols.
            </p>
          </div>
        )}

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
            <label className="text-sm font-medium">{mvLabel}</label>
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

      {data && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              Date {data.trade_date} · {data.matched}/{data.universe_size} matched · showing{" "}
              {data.count}
            </span>
            <span>Source: {data.source}</span>
            <span>
              Cap unit: {data.market_cap_unit === "CNY_yi" ? "亿元" : "USD"}
            </span>
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
                    <th className="px-3 py-2 font-medium text-right">Yield %</th>
                    <th className="px-3 py-2 font-medium text-right">PE</th>
                    <th className="px-3 py-2 font-medium text-right">PB</th>
                    <th className="px-3 py-2 font-medium text-right">
                      Mkt Cap ({data.market_cap_unit === "CNY_yi" ? "亿元" : "USD"})
                    </th>
                    <th className="px-3 py-2 font-medium text-right">Close</th>
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((row, i) => (
                    <tr key={row.code} className="border-b last:border-0 hover:bg-muted/20">
                      <td className="px-3 py-2 text-muted-foreground">{i + 1}</td>
                      <td className="px-3 py-2 font-mono">{row.code}</td>
                      <td className="px-3 py-2">{row.name || "—"}</td>
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
                        {fmtCap(row.market_cap, data.market_cap_unit)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {fmt(row.close)}
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

function fmt(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}

function fmtCap(v: number | null, unit: string): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (unit === "USD") {
    if (v >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
    if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    return v.toLocaleString();
  }
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
