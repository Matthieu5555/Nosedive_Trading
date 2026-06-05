export interface ChartPoint {
  x: number;
  y: number;
}

export interface ChartSeries {
  id: string;
  points: ChartPoint[];
  color?: string;
}

interface LineChartProps {
  series: ChartSeries[];
  ariaLabel: string;
  markerX?: number;
  formatY?: (value: number) => string;
  formatX?: (value: number) => string;
}

const WIDTH = 320;
const HEIGHT = 136;
const PAD = { top: 10, right: 12, bottom: 22, left: 48 };
const PALETTE = ["var(--blue)", "var(--amber)", "var(--positive)", "var(--negative)"];

function defaultFormat(value: number): string {
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

export function LineChart({
  series,
  ariaLabel,
  markerX,
  formatY = defaultFormat,
  formatX = defaultFormat,
}: LineChartProps) {
  const all = series.flatMap((item) => item.points);
  if (all.length === 0) return <div className="state-panel">No data</div>;

  const xMin = Math.min(...all.map((point) => point.x));
  const xMax = Math.max(...all.map((point) => point.x));
  const yMin = Math.min(...all.map((point) => point.y));
  const yMax = Math.max(...all.map((point) => point.y));
  const ySpread = Math.max(yMax - yMin, 1e-9);
  // Geometric padding only: axis labels keep the true data min/max.
  const yLow = yMin - ySpread * 0.08;
  const yHigh = yMax + ySpread * 0.08;
  const innerWidth = WIDTH - PAD.left - PAD.right;
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;
  const toX = (x: number) => PAD.left + ((x - xMin) / Math.max(xMax - xMin, 1e-9)) * innerWidth;
  const toY = (y: number) => PAD.top + (1 - (y - yLow) / (yHigh - yLow)) * innerHeight;
  const showZeroLine = yLow < 0 && yHigh > 0;
  const showMarker = markerX !== undefined && markerX >= xMin && markerX <= xMax;

  return (
    <svg
      className="line-chart"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={ariaLabel}
    >
      <line
        className="chart-axis"
        x1={PAD.left}
        y1={PAD.top}
        x2={PAD.left}
        y2={HEIGHT - PAD.bottom}
      />
      <line
        className="chart-axis"
        x1={PAD.left}
        y1={HEIGHT - PAD.bottom}
        x2={WIDTH - PAD.right}
        y2={HEIGHT - PAD.bottom}
      />
      {showZeroLine && (
        <line
          className="chart-zero"
          x1={PAD.left}
          y1={toY(0)}
          x2={WIDTH - PAD.right}
          y2={toY(0)}
        />
      )}
      {showMarker && (
        <line
          className="chart-marker"
          x1={toX(markerX)}
          y1={PAD.top}
          x2={toX(markerX)}
          y2={HEIGHT - PAD.bottom}
        />
      )}
      {series.map((item, index) => {
        const color = item.color ?? PALETTE[index % PALETTE.length];
        const path = item.points
          .map((point) => `${toX(point.x).toFixed(1)},${toY(point.y).toFixed(1)}`)
          .join(" ");
        const last = item.points[item.points.length - 1];
        return (
          <g key={item.id}>
            <polyline className="series-line" points={path} style={{ stroke: color }} />
            {last && (
              <circle
                className="series-dot"
                cx={toX(last.x)}
                cy={toY(last.y)}
                r={2.4}
                style={{ fill: color }}
              />
            )}
          </g>
        );
      })}
      <text className="chart-label" x={PAD.left - 5} y={toY(yMax) + 3} textAnchor="end">
        {formatY(yMax)}
      </text>
      <text className="chart-label" x={PAD.left - 5} y={toY(yMin) + 3} textAnchor="end">
        {formatY(yMin)}
      </text>
      <text className="chart-label" x={PAD.left} y={HEIGHT - 7} textAnchor="start">
        {formatX(xMin)}
      </text>
      <text className="chart-label" x={WIDTH - PAD.right} y={HEIGHT - 7} textAnchor="end">
        {formatX(xMax)}
      </text>
    </svg>
  );
}
