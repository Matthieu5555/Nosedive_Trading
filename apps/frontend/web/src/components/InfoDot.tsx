import { type ReactNode, useId, useState } from "react";

interface InfoDotProps {
  label: string;
  body: ReactNode;
  className?: string;
}

export function InfoDot({ label, body, className }: InfoDotProps) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();

  if (body === null || body === undefined || body === "" || body === false) {
    return null;
  }

  function show() {
    setOpen(true);
  }
  function hide() {
    setOpen(false);
  }

  return (
    <span className={className ? `info-dot-wrap ${className}` : "info-dot-wrap"}>
      <button
        type="button"
        className="info-dot"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(event) => {
          if (event.key === "Escape") hide();
        }}
      >
        <span aria-hidden="true">ⓘ</span>
      </button>
      {open ? (
        <span id={tooltipId} role="tooltip" className="info-tooltip">
          {body}
        </span>
      ) : null}
    </span>
  );
}
