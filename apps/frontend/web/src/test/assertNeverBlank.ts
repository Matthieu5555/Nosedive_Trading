import { expect } from "vitest";

interface RenderLike {
  container: HTMLElement;
}

export function assertNeverBlank(rendered: RenderLike): void {
  const { container } = rendered;
  const hasText = (container.textContent ?? "").trim().length > 0;
  const hasStatusRole =
    container.querySelector("[role='status'],[role='alert'],[aria-busy='true']") !== null;
  expect(
    hasText || hasStatusRole,
    "surface rendered blank: no visible text and no status/alert/busy role",
  ).toBe(true);
}
