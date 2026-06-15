// shadcn/ui Button — copy-in primitive, themed onto the operator-console dark palette.
// Lives in the `ui` layer; imports only `@/lib/utils` (lib) which the boundary DAG allows.
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:pointer-events-none disabled:text-faint [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border border-border-strong bg-panel-soft text-panel-soft-text hover:opacity-90",
        outline: "border border-border-strong bg-[#111311] text-text hover:border-text/30",
        ghost: "border border-transparent text-muted hover:text-text",
        destructive:
          "border border-border-strong bg-[#111311] text-negative hover:border-negative/40",
      },
      size: {
        default: "min-h-[38px] px-4",
        sm: "min-h-[32px] px-3 text-xs",
        lg: "min-h-[44px] px-6",
        icon: "size-[38px]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
