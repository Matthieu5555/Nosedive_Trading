import type { CSSProperties } from "react";

export const SKELETON_DELAY_MS = 1000;

interface SkeletonProps {
  height?: number;
  width?: string | number;
  className?: string;
  label?: string;
}

export function Skeleton({ height = 440, width, className, label = "Chargement…" }: SkeletonProps) {
  const style: CSSProperties = { height };
  if (width !== undefined) style.width = width;
  return (
    <div
      className={className ? `chart-skeleton ${className}` : "chart-skeleton"}
      style={style}
      role="status"
      aria-busy="true"
      aria-label={label}
    >
      <span className="chart-skeleton__label">{label}</span>
    </div>
  );
}

interface ChartSkeletonProps {
  height?: number;
  subject?: string;
}

export function ChartSkeleton({ height = 440, subject }: ChartSkeletonProps) {
  const label = subject ? `Chargement de ${subject}…` : "Chargement…";
  return <Skeleton height={height} label={label} />;
}
