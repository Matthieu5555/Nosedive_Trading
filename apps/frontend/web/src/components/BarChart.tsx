export interface ChartBar {
  label: string;
  value: number;
}

interface BarChartProps {
  bars: ChartBar[];
  ariaLabel: string;
  formatValue?: (value: number) => string;
}

const WIDTH = 320;
const HEIGHT = 136;
const PAD = { top: 16, right: 8, bottom: 22, left: 8 };

function defaultFormat(value: number): string {
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function BarChart({ bars, ariaLabel, formatValue = defaultFormat }: BarChartProps) {
  if (bars.length === 0) return <div className="state-panel">No data</div>;

  const low = Math.min(0, ...bars.map((bar) => bar.value));
  const high = Math.max(0, ...bars.map((bar) => bar.value));
  const spread = Math.max(high - low, 1e-9);
  const innerWidth = WIDTH - PAD.left - PAD.right;
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;
  const toY = (value: number) => PAD.top + (1 - (value - low) / spread) * innerHeight;
  const slot = innerWidth / bars.length;
  const barWidth = Math.min(slot * 0.55, 56);

  return (
    <svg className="bar-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={ariaLabel}>
      <line className="chart-zero" x1={PAD.left} y1={toY(0)} x2={WIDTH - PAD.right} y2={toY(0)} />
      {bars.map((bar, index) => {
        const center = PAD.left + slot * index + slot / 2;
        const top = Math.min(toY(bar.value), toY(0));
        const height = Math.max(Math.abs(toY(bar.value) - toY(0)), 1);
        return (
          <g key={bar.label}>
            <rect
              className={bar.value >= 0 ? "bar positive-bar" : "bar negative-bar"}
              x={center - barWidth / 2}
              y={top}
              width={barWidth}
              height={height}
              rx={2}
            />
            {/* Always above the bar's top edge (the zero line for negative bars),
                so value labels never collide with the x-axis labels below. */}
            <text className="chart-label" x={center} y={top - 4} textAnchor="middle">
              {formatValue(bar.value)}
            </text>
            <text className="chart-label" x={center} y={HEIGHT - 7} textAnchor="middle">
              {bar.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
