import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { Job } from "../../api";
import { JobNotice } from "./JobNotice";

function job(overrides: Partial<Job>): Job {
  return {
    job_id: "j1",
    provider: "SAMPLE",
    underlying: "SX5E",
    state: "running",
    started_at: "2026-06-17T17:30:00",
    finished_at: null,
    message: "",
    summary: {},
    ...overrides,
  };
}

describe("JobNotice", () => {
  test("a running job alone announces nothing yet (no premature notice)", () => {
    render(<JobNotice jobs={[job({ state: "running" })]} />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("running -> done emits a polite, non-blocking done notice naming the underlying", () => {
    const { rerender } = render(<JobNotice jobs={[job({ state: "running" })]} />);
    rerender(<JobNotice jobs={[job({ state: "done" })]} />);
    const notice = screen.getByRole("status");
    expect(notice).toHaveAttribute("aria-live", "polite");
    expect(notice).toHaveTextContent(/SX5E capture complete/);
    // Non-blocking: no modal/dialog in the tree.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("running -> error emits a loud alert carrying the failure message", () => {
    const { rerender } = render(<JobNotice jobs={[job({ state: "running" })]} />);
    rerender(<JobNotice jobs={[job({ state: "error", message: "no committed sample day" })]} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/SX5E capture failed/);
    expect(alert).toHaveTextContent(/no committed sample day/);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("a ledger first seen already-terminal stays quiet (a tab revisit doesn't re-announce)", () => {
    render(<JobNotice jobs={[job({ state: "done" })]} />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("an empty ledger renders nothing", () => {
    const { container } = render(<JobNotice jobs={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
