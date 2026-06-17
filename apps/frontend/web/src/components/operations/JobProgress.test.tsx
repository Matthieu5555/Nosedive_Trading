import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { Job } from "../../api";
import { JobProgress } from "./JobProgress";

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

describe("JobProgress", () => {
  test("a determinate stage renders étape k/N, the stage label, and the right percent", () => {
    // 2 of 4 stages reached -> 50% (derived by hand, not read from the component).
    render(
      <JobProgress
        job={job({ stage: "Collecte de la chaîne d'options", stage_index: 2, stage_total: 4 })}
      />,
    );
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(screen.getByText(/étape 2\/4/)).toBeInTheDocument();
    expect(screen.getByText(/Collecte de la chaîne d'options/)).toBeInTheDocument();
    expect(screen.getByText(/50%/)).toBeInTheDocument();
  });

  test("the final reached stage reads 100% (4 of 4)", () => {
    render(
      <JobProgress
        job={job({ stage: "Récapitulatif de la nappe", stage_index: 4, stage_total: 4 })}
      />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText(/100%/)).toBeInTheDocument();
  });

  test("a null stage degrades to an honest indeterminate bar with no percent", () => {
    render(<JobProgress job={job({ stage: null, stage_index: null, stage_total: null })} />);
    expect(screen.getByText("en cours…")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
    // No fabricated percent anywhere — the indeterminate case never claims progress.
    expect(screen.queryByText(/%/)).toBeNull();
  });

  test("a payload missing the stage fields entirely (back-compat) is indeterminate, not a crash", () => {
    render(<JobProgress job={job({})} />);
    expect(screen.getByText("en cours…")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  test("a stage_total of 0 is not trusted — degrades to indeterminate, never divides by zero", () => {
    render(<JobProgress job={job({ stage: "x", stage_index: 0, stage_total: 0 })} />);
    expect(screen.getByText("en cours…")).toBeInTheDocument();
  });

  test("a done job renders no progress bar (the row shows its summary, never a stale bar)", () => {
    const { container } = render(
      <JobProgress job={job({ state: "done", stage: "fini", stage_index: 4, stage_total: 4 })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  test("an error job renders no progress bar", () => {
    const { container } = render(<JobProgress job={job({ state: "error", message: "boom" })} />);
    expect(container).toBeEmptyDOMElement();
  });
});
