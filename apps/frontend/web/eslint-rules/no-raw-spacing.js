// Local ESLint plugin: the spacing guardrail.
//
// The console has ONE spacing scale — the `--space-*` CSS custom properties, mirrored as named
// Tailwind utilities (p-md, gap-sm, mt-lg, …). Everything that adds whitespace between or around
// boxes must come from that scale, so the whole UI snaps to a single rhythm and a designer can
// retune spacing in one place. Agents keep escaping the scale two ways:
//
//   1. raw px in an inline JSX `style={{ padding: "6px" }}` (or a bare numeric `style={{ gap: 8 }}`)
//   2. arbitrary Tailwind spacing classes like `p-[18px]` / `gap-[6px]`
//
// Both let the call site express an off-scale value, which is exactly what we want to make
// impossible. This rule bans precisely those two forms — nothing else. It deliberately does NOT
// touch positioning (top/left), sizing (width/height), typography, borders or shadows, and it
// deliberately does NOT touch the legacy numeric Tailwind utilities (p-2, gap-4) — that is a
// separate, larger migration.

// Spacing-only CSS properties (camelCase, as they appear in a JSX style object).
const SPACING_PROPS = new Set([
  "margin",
  "marginTop",
  "marginRight",
  "marginBottom",
  "marginLeft",
  "marginInline",
  "marginInlineStart",
  "marginInlineEnd",
  "marginBlock",
  "marginBlockStart",
  "marginBlockEnd",
  "padding",
  "paddingTop",
  "paddingRight",
  "paddingBottom",
  "paddingLeft",
  "paddingInline",
  "paddingInlineStart",
  "paddingInlineEnd",
  "paddingBlock",
  "paddingBlockStart",
  "paddingBlockEnd",
  "gap",
  "rowGap",
  "columnGap",
]);

// String values that are on-scale (or trivially safe) and therefore allowed.
//   - var(--space-*) is the named scale itself.
//   - the composed page/panel tokens are built FROM the scale.
//   - 0 / "0" / "auto" carry no off-scale magnitude.
//   - calc(...) is allowed only when it references a --space token (so it stays on-scale).
const ALLOWED_VALUE = /^(0|auto)$/;
const ALLOWED_VAR = /^var\(--(space-[a-z0-9-]+|page-gap|panel-pad|panel-gap)\)$/;
const SPACE_CALC = /calc\([^)]*var\(--space/;
// Does a string value carry a raw px magnitude? ("6px", "12px 14px", "-4px")
const HAS_PX = /-?\d*\.?\d+px/;

// Arbitrary-value Tailwind spacing utilities: the bracketed form p-[18px], gap-[6px], -mt-[3px], …
// The bracket is the tell — named (p-md) and numeric (p-2) utilities never use it.
const ARBITRARY_TW_SPACING =
  /(^|[\s"'`])-?(p|px|py|pt|pb|pl|pr|ps|pe|m|mx|my|mt|mb|ml|mr|ms|me|gap|gap-x|gap-y|space-x|space-y)-\[[^\]]*\]/;

const STYLE_MSG =
  "Off-scale spacing: use the named scale (p-md / gap-sm) or a var(--space-*) value in style, not a raw px / bare number.";
const CLASS_MSG =
  "Off-scale Tailwind spacing utility: use a named utility (p-md / gap-sm) or var(--space-*), not an arbitrary `[..]` value.";

const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Ban off-scale spacing: raw px in inline style spacing props and arbitrary Tailwind spacing utilities.",
    },
    schema: [],
    messages: {
      style: STYLE_MSG,
      class: CLASS_MSG,
    },
  },
  create(context) {
    function checkStringForArbitraryTw(node, raw) {
      if (typeof raw !== "string") return;
      if (ARBITRARY_TW_SPACING.test(raw)) {
        context.report({ node, messageId: "class" });
      }
    }

    return {
      // 1. Inline style spacing props with a raw px string or a bare positive numeric literal.
      Property(node) {
        // Only object-literal properties whose key is a spacing property.
        let keyName = null;
        if (node.key.type === "Identifier") keyName = node.key.name;
        else if (node.key.type === "Literal" && typeof node.key.value === "string")
          keyName = node.key.value;
        if (!keyName || !SPACING_PROPS.has(keyName)) return;

        const value = node.value;
        if (value.type === "Literal" && typeof value.value === "string") {
          const v = value.value.trim();
          if (ALLOWED_VALUE.test(v) || ALLOWED_VAR.test(v) || SPACE_CALC.test(v)) return;
          if (HAS_PX.test(v)) {
            context.report({ node: value, messageId: "style" });
          }
          return;
        }
        if (value.type === "Literal" && typeof value.value === "number") {
          // A bare number on a spacing prop is px in React; 0 is fine, any other magnitude is off-scale.
          if (value.value > 0) {
            context.report({ node: value, messageId: "style" });
          }
        }
      },

      // 2. Arbitrary Tailwind spacing utilities anywhere in string / template literals. The pattern
      // is specific enough (utility prefix + bracket) that scanning all literals is safe.
      Literal(node) {
        if (typeof node.value === "string") {
          checkStringForArbitraryTw(node, node.value);
        }
      },
      TemplateElement(node) {
        checkStringForArbitraryTw(node, node.value && node.value.raw);
      },
    };
  },
};

export default {
  rules: {
    "no-raw-spacing": rule,
  },
};
