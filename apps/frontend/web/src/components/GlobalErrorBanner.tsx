// The always-visible failure surface. Mounted once at the app root (src/main.tsx), it renders a
// dismissible alert for every failure that escapes the in-page tiles — uncaught errors, unhandled
// rejections, background query failures. It is the single component that makes the "no silent
// failure" guarantee true: whatever breaks, an operator sees a labelled notice, never just a dead
// page. It owns no failures of its own — it only reflects the runtimeErrors pub-sub.

import { useEffect, useState } from "react";

import {
  dismissRuntimeError,
  type RuntimeError,
  subscribeRuntimeErrors,
} from "../lib/runtimeErrors";

export function GlobalErrorBanner() {
  const [errors, setErrors] = useState<readonly RuntimeError[]>([]);
  useEffect(() => subscribeRuntimeErrors(setErrors), []);

  if (errors.length === 0) return null;
  return (
    <div className="global-error-banner" role="alert" aria-live="assertive">
      {errors.map((error) => (
        <div key={error.id} className="global-error-banner-item">
          <span className="global-error-banner-message">{error.message}</span>
          <button
            type="button"
            className="link-button"
            aria-label="Dismiss error"
            onClick={() => dismissRuntimeError(error.id)}
          >
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
