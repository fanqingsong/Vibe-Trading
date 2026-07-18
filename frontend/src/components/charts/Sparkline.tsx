interface SparkPoint {
  date: string;
  close: number;
}

interface Props {
  points: SparkPoint[];
  /** Optional horizontal reference (e.g. prior high). */
  priorHigh?: number | null;
  /** Highlight this session on the path. */
  signalDate?: string | null;
  width?: number;
  height?: number;
  className?: string;
}

/** Lightweight SVG sparkline — avoids spinning up ECharts per table row. */
export function Sparkline({
  points,
  priorHigh,
  signalDate,
  width = 120,
  height = 36,
  className,
}: Props) {
  if (!points || points.length < 2) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const values = points.map((p) => p.close);
  const min = Math.min(...values, priorHigh ?? values[0]);
  const max = Math.max(...values, priorHigh ?? values[0]);
  const pad = 2;
  const span = max - min || 1;
  const n = points.length;

  const xAt = (i: number) => pad + (i / (n - 1)) * (width - pad * 2);
  const yAt = (v: number) =>
    pad + (1 - (v - min) / span) * (height - pad * 2);

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(p.close).toFixed(1)}`)
    .join(" ");

  const up = values[values.length - 1] >= values[0];
  const stroke = up ? "hsl(var(--success))" : "hsl(var(--danger))";
  const signalIdx = signalDate
    ? points.findIndex((p) => p.date === signalDate)
    : -1;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden
    >
      {priorHigh != null && priorHigh > 0 && (
        <line
          x1={pad}
          x2={width - pad}
          y1={yAt(priorHigh)}
          y2={yAt(priorHigh)}
          stroke="currentColor"
          strokeOpacity={0.35}
          strokeDasharray="3 2"
          strokeWidth={1}
        />
      )}
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.5} />
      {signalIdx >= 0 && (
        <circle
          cx={xAt(signalIdx)}
          cy={yAt(points[signalIdx].close)}
          r={2.5}
          fill={stroke}
        />
      )}
    </svg>
  );
}
