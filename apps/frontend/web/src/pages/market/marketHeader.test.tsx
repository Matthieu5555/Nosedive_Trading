import { expect, test } from "vitest";

import type { AvailableDate } from "../../api";
import { fetchOptionLabels } from "./marketHeader";

const fetchRow = (over: Partial<AvailableDate>): AvailableDate => ({
  date: "2026-06-16",
  run_id: "r1",
  recorded_ts: "2026-06-16T17:30:00",
  qc: "pass",
  ...over,
});

test("labels each fetch as '<date> · <HH:MM:SS>', keyed by run_id", () => {
  const labels = fetchOptionLabels([
    fetchRow({ run_id: "r-late", recorded_ts: "2026-06-16T18:05:12" }),
    fetchRow({ run_id: "r-early", recorded_ts: "2026-06-16T17:30:00" }),
  ]);
  expect(labels.get("r-late")).toBe("2026-06-16 · 18:05:12");
  expect(labels.get("r-early")).toBe("2026-06-16 · 17:30:00");
});

test("two fetches that land in the same second are tie-broken by short run_id", () => {
  const labels = fetchOptionLabels([
    fetchRow({ run_id: "aaaaaaaa1111", recorded_ts: "2026-06-16T17:30:00" }),
    fetchRow({ run_id: "bbbbbbbb2222", recorded_ts: "2026-06-16T17:30:00" }),
  ]);
  expect(labels.get("aaaaaaaa1111")).toBe("2026-06-16 · 17:30:00 (aaaaaaaa)");
  expect(labels.get("bbbbbbbb2222")).toBe("2026-06-16 · 17:30:00 (bbbbbbbb)");
});

test("a missing recorded_ts renders an em-dash time, and QC verdict is suffixed", () => {
  const labels = fetchOptionLabels([fetchRow({ run_id: "r-na", recorded_ts: null, qc: "fail" })]);
  expect(labels.get("r-na")).toBe("2026-06-16 · — (QC fail)");
});
