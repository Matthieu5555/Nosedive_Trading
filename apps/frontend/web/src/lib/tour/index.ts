// The guided-tour module. Authors touch exactly one thing, tourAnchor, to make an element
// highlightable; the assistant loop reads the catalog back off the DOM. There is no static registry
// to keep in sync (see anchor.ts).
export {
  TOUR_DESC_ATTR,
  TOUR_ID_ATTR,
  TOUR_LABEL_ATTR,
  tourAnchor,
  type TourAnchorProps,
} from "./anchor";
export { tourCatalog, type TourCatalogEntry } from "./catalog";
