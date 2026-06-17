export const PALETTE_FALLBACK = {
  bg: "#0e100e",
  panel: "#151715",
  panelSoft: "#e5eee1",
  panelSoftText: "#121511",
  border: "#2b302c",
  borderStrong: "#454d45",
  text: "#f2f5ef",
  muted: "#8f978f",
  faint: "#5f6860",
  positive: "#7fd99a",
  negative: "#f08a7e",
  amber: "#e8c264",
  blue: "#79b8d6",
} as const;

export type TokenName = keyof typeof PALETTE_FALLBACK;

const CSS_VAR: Record<TokenName, string> = {
  bg: "--bg",
  panel: "--panel",
  panelSoft: "--panel-soft",
  panelSoftText: "--panel-soft-text",
  border: "--border",
  borderStrong: "--border-strong",
  text: "--text",
  muted: "--muted",
  faint: "--faint",
  positive: "--positive",
  negative: "--negative",
  amber: "--amber",
  blue: "--blue",
};

export const RADIUS_FALLBACK = "8px";

function readVar(name: string): string {
  if (typeof document === "undefined" || typeof getComputedStyle !== "function") {
    return "";
  }
  const root = document.documentElement;
  if (!root) return "";
  return getComputedStyle(root).getPropertyValue(name).trim();
}

export function token(name: TokenName): string {
  const fromCss = readVar(CSS_VAR[name]);
  return fromCss || PALETTE_FALLBACK[name];
}

export function radius(): string {
  const fromCss = readVar("--radius");
  return fromCss || RADIUS_FALLBACK;
}

export function palette(): Record<TokenName, string> {
  const out = {} as Record<TokenName, string>;
  for (const name of Object.keys(PALETTE_FALLBACK) as TokenName[]) {
    out[name] = token(name);
  }
  return out;
}

export const SIGN_TONE = {
  positive: "positive",
  negative: "negative",
  amber: "amber",
  blue: "blue",
  neutral: "muted",
} as const;

export type SignTone = keyof typeof SIGN_TONE;
