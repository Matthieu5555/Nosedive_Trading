import { type ReactNode } from "react";

interface PulseHintProps {
  active: boolean;
  label?: string;
  children: ReactNode;
}

export function PulseHint({ active, label, children }: PulseHintProps) {
  if (!active) {
    return <>{children}</>;
  }
  return (
    <span className="pulse-hint" data-pulse-hint="active" role="note" aria-label={label}>
      {children}
    </span>
  );
}
