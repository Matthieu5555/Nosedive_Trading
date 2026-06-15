// cn() — the single class-name combiner for shadcn/ui primitives and any Tailwind-using
// component. clsx resolves conditional/array class inputs; tailwind-merge then dedupes
// conflicting Tailwind utilities (last-wins, e.g. `px-2 px-4` → `px-4`) so callers can
// override a primitive's defaults via the `className` prop without specificity battles.
//
// Lives in the `lib` layer (pure, framework-free) so every upper layer — including `ui` —
// may import it without breaking the enforced boundary DAG.
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
