import { type ReactNode } from "react";

import { InfoDot } from "../InfoDot";

interface HotspotProps {
  label: string;
  body: ReactNode;
  className?: string;
}

export function Hotspot({ label, body, className }: HotspotProps) {
  return (
    <span className={className ? `hotspot ${className}` : "hotspot"}>
      <InfoDot label={label} body={body} />
    </span>
  );
}
