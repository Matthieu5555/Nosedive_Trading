import { Maximize2 } from "lucide-react";
import { type ReactNode, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../ui/dialog";

// The shared "Full screen" mechanism for a dense table. A small, PM-legible Full-screen affordance
// opens the same table at full size in a centred dialog, so the whole grid is readable without the
// inline panel's max-height scroll. The inline table is unchanged. The Price-structure order book and
// the Dollar Greeks table both mount this, so the two fullscreen controls are byte-for-byte the same
// button and the same dialog container/behaviour.
export function TableExpand({
  title,
  description,
  triggerLabel = "Open the full table",
  children,
}: {
  title: ReactNode;
  description: ReactNode;
  // The accessible label on the trigger button (defaults to a generic full-table phrasing).
  triggerLabel?: string;
  // The full-size table to render inside the dialog's scroll container.
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger className="price-structure-expand" aria-label={triggerLabel}>
        <Maximize2 className="price-structure-expand__icon" aria-hidden="true" />
        <span>Full screen</span>
      </DialogTrigger>
      <DialogContent className="price-structure-dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="price-structure-dialog__scroll">{children}</div>
      </DialogContent>
    </Dialog>
  );
}
