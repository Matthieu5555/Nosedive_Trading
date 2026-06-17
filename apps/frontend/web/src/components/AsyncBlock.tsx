import { type ReactNode, useEffect, useState } from "react";

import { ChartSkeleton, SKELETON_DELAY_MS } from "./Skeleton";

interface AsyncBlockProps {
  loading: boolean;
  error: string | null;
  children: ReactNode;
  height?: number;
  subject?: string;
}

export function AsyncBlock({ loading, error, children, height, subject }: AsyncBlockProps) {
  const [skeletonVisible, setSkeletonVisible] = useState(false);

  useEffect(() => {
    if (!loading) {
      setSkeletonVisible(false);
      return;
    }
    const timer = setTimeout(() => setSkeletonVisible(true), SKELETON_DELAY_MS);
    return () => clearTimeout(timer);
  }, [loading]);

  if (loading) {
    if (!skeletonVisible) {
      return <div className="chart-skeleton-pending" role="status" aria-busy="true" />;
    }
    return <ChartSkeleton height={height} subject={subject} />;
  }
  if (error) {
    return (
      <div className="state-panel state-panel-error" role="alert">
        {error}
      </div>
    );
  }
  return <>{children}</>;
}
