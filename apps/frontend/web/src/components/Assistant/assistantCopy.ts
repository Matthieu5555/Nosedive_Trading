import type { AssistantMode } from "./assistantApi";

// The screen-aware copy map. One entry per on-screen element id, in PM register, written ONCE here
// and consumed by the assistant panel's "what is this?" answer (and, once the canonical lib/explain.ts
// from MAT-LEGIBILITY-explanation-map lands, lifted into it verbatim — this is shaped as that map's
// ExplainEntry so the move is a rename, not a rewrite). The prose is lifted from the inline constants
// the components already render (charts.tsx SURFACE_LABEL / SMILE_HEAD / GREEKS_SHAPE_HEAD, the
// TenorPanel convexity gloss, the Scorecards hints + sign legend, api.ts SIGNAL_CAPTIONS) so the
// tooltip and the assistant can never say different things about the same number.
//
// HARD GUARDRAIL: an entry carries NO numeric value of its own — only what/how-to-read/unit and a
// whereFrom() that formats the live context handed to it. The number the assistant quotes always
// comes from the server-built facts block (the citations), never baked into this copy. That is how
// "the assistant never invents a number" is enforced in the data layer, not the model's goodwill.

export interface ExplainContext {
  underlying: string;
  asOf: string;
  closeInstant: string | null;
  mode: AssistantMode;
  tenor?: string | null;
  source?: "signal" | "projected" | null;
  hasValue?: boolean;
}

export interface ExplainEntry {
  label: string;
  whatIs: string;
  howToRead: string;
  unit: string | null;
  whereFrom: (ctx: ExplainContext) => string;
}

// SX5E close is 17:30 CET (OESX settlement), not 22:00. The instant is resolved server-side and
// carried on the frame; this only renders what it was handed — it never re-derives a close time.
function asOfClause(ctx: ExplainContext): string {
  const instant = ctx.closeInstant ?? `${ctx.asOf} 17:30 CET`;
  return `clôture ${instant}`;
}

function modeClause(ctx: ExplainContext): string {
  return ctx.mode === "indicative"
    ? "marque INDICATIVE — pas la clôture stockée"
    : "clôture stockée (strict)";
}

function signalWhereFrom(ctx: ExplainContext): string {
  if (ctx.hasValue === false) {
    return `signal non enregistré pour cette clôture (${ctx.underlying})`;
  }
  const tenor = ctx.tenor ? ` · ${ctx.tenor}` : "";
  const provenance =
    ctx.source === "projected"
      ? `projeté depuis le smile${tenor}`
      : `signal enregistré${tenor}`;
  return `${provenance} · ${asOfClause(ctx)} · ${modeClause(ctx)}`;
}

function surfaceWhereFrom(ctx: ExplainContext): string {
  return `${ctx.underlying} · ${asOfClause(ctx)} · ${modeClause(ctx)}`;
}

export const ASSISTANT_COPY: Record<string, ExplainEntry> = {
  nappe: {
    label: "Nappe de volatilité",
    whatIs: "La surface de vol implicite : vol vs log-moneyness vs maturité.",
    howToRead:
      "Une bosse ou un creux montre où le marché price le risque ; les bords lointains reposent sur moins de cotations.",
    unit: "Vol",
    whereFrom: surfaceWhereFrom,
  },
  smile: {
    label: "Smile",
    whatIs: "La vol implicite vs log-moneyness à une maturité : puts ◄ ATM ► calls.",
    howToRead:
      "Un sourire prononcé côté puts signale une demande de protection à la baisse.",
    unit: "Vol",
    whereFrom: surfaceWhereFrom,
  },
  greek_profiles: {
    label: "Profils de Greeks",
    whatIs: "Les Greeks bruts vs strike : cloche gamma/vega, S-curve delta.",
    howToRead:
      "Le pic de gamma/vega est à la monnaie ; le delta passe de 0 à 1 en traversant l'ATM.",
    unit: null,
    whereFrom: surfaceWhereFrom,
  },
  atm_level: {
    label: "Vol à la monnaie",
    whatIs: "La vol implicite à la monnaie, à la maturité de référence.",
    howToRead: "Le niveau de cherté des options : plus haut = options plus chères.",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  skew_25d: {
    label: "Skew 25Δ",
    whatIs: "L'écart de vol entre le put 25Δ et le call 25Δ, en points de vol.",
    howToRead: "Positif = la protection à la baisse coûte plus cher que la hausse.",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  rv_minus_iv: {
    label: "RV − IV",
    whatIs:
      "La vol récemment réalisée moins la vol implicite, en points de vol.",
    howToRead:
      "> 0 = vol bon marché (acheter) ; le marché a bougé plus que les options ne le pricaient.",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  iv_rank: {
    label: "Rang d'IV",
    whatIs:
      "Où la vol implicite du jour se situe dans son intervalle sur 1 an, 0–100%.",
    howToRead: "Élevé = les options paraissent chères face à l'année écoulée.",
    unit: null,
    whereFrom: signalWhereFrom,
  },
  term_structure_slope: {
    label: "Pente de la structure par terme",
    whatIs:
      "La vol implicite long-terme moins court-terme, en points de vol.",
    howToRead:
      "Positive (pente montante) = marché calme ; négative = stress à court terme.",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  rho_bar: {
    label: "Corrélation implicite (ρ̄)",
    whatIs:
      "La corrélation implicite moyenne entre les membres de l'indice, −1 à +1.",
    howToRead:
      "Élevée = les valeurs sont censées bouger ensemble, l'indice paraît cher face à ses composants.",
    unit: null,
    whereFrom: signalWhereFrom,
  },
  convexity_25d: {
    label: "Convexité 25Δ (papillon)",
    whatIs: "IV(25Δp) + IV(25Δc) − 2·ATM (vp = point de vol = 0.01 IV).",
    howToRead:
      "Positive = sourire convexe ; les ailes coûtent plus que la monnaie.",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  surface_coverage: {
    label: "Couverture de la nappe",
    whatIs:
      "La fraction des cotations captées sur laquelle la nappe repose réellement (deux-faces / total).",
    howToRead:
      "Pleine = surface solide ; partielle = certaines mailles reposent sur peu de cotations ; dégénérée = marché probablement fermé.",
    unit: null,
    whereFrom: surfaceWhereFrom,
  },
};

export function explainEntry(id: string): ExplainEntry | null {
  return ASSISTANT_COPY[id] ?? null;
}

// The grounded seam: assemble what/how-to-read/where-from for a live context. Carries NO number —
// the value is the citation's, rendered server-side. An unknown id returns null (a typed "unknown
// metric"), never free text, so the assistant cannot reference an element outside this closed
// vocabulary. This mirrors the canonical explainWithContext(id, ctx) the explanation-map spec owns.
export function explainWithContext(id: string, ctx: ExplainContext): string | null {
  const entry = explainEntry(id);
  if (!entry) return null;
  return `${entry.whatIs} ${entry.howToRead} (${entry.whereFrom(ctx)})`;
}

export function knownElementIds(): string[] {
  return Object.keys(ASSISTANT_COPY);
}
