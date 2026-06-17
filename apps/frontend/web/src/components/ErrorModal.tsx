// The always-on failure surface, as a CENTERED MODAL (not the old top strip).
//
// The owner: "THE 404 NOT FOUND AT THE TOP IS UGLY, IT'S IN FRONT OF THE BANNER. I want a rectangle
// centered in the middle of the screen, with the Dismiss button directly below it." This component
// is that: it subscribes to the same runtimeErrors pub-sub the old banner used (so it keeps the
// "no silent failure" guarantee), and renders the newest unresolved failure as a centered card with
// the message, an optional "+N more" note, and a single Dismiss button stacked directly under it.
//
// Layering: the scrim sits at --z-modal (above page content and above any legacy banner), so the
// notice is unmistakable and never buried behind the sticky topbar the way the old strip was.

import { useEffect, useState } from "react";

import {
  dismissRuntimeError,
  type RuntimeError,
  subscribeRuntimeErrors,
} from "../lib/runtimeErrors";

export function ErrorModal() {
  const [errors, setErrors] = useState<readonly RuntimeError[]>([]);
  useEffect(() => subscribeRuntimeErrors(setErrors), []);

  if (errors.length === 0) return null;
  // Show the newest failure front-and-centre; older unresolved ones are summarised, so a retry
  // storm shows one card, not a stack. Dismiss clears the one on top, revealing the next.
  const current = errors[errors.length - 1];
  const more = errors.length - 1;

  return (
    <div className="error-modal__scrim" role="alertdialog" aria-modal="true" aria-live="assertive">
      <div className="error-modal__card" aria-labelledby="error-modal-title">
        <p id="error-modal-title" className="error-modal__title">
          Something went wrong
        </p>
        <p className="error-modal__message">{current.message}</p>
        {more > 0 && (
          <p className="error-modal__more">
            {more} earlier {more === 1 ? "error" : "errors"} also waiting.
          </p>
        )}
        <button
          type="button"
          className="error-modal__dismiss"
          aria-label="Dismiss error"
          autoFocus
          onClick={() => dismissRuntimeError(current.id)}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
