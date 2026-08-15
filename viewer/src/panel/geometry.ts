/**
 * Screen-space arithmetic for the floating panel, kept pure so it is testable without a DOM.
 *
 * Two representations, deliberately: the panel is *stored* as an offset from whichever frame
 * corner it is nearest (`PanelGeometry`), because that is what survives a window resize — park it
 * against the bottom-right and it stays there when the frame shrinks. It is *drawn* and dragged as
 * a plain left/top rectangle (`Rect`), because that is what pointer maths and CSS want. Everything
 * here is CSS pixels relative to the app frame; nothing here knows the camera exists.
 */

import { PANEL_HEIGHT_RANGE, PANEL_WIDTH_RANGE } from "../types";
import type { PanelAnchor, PanelGeometry } from "../types";

/** A panel box in frame coordinates: distance from the frame's top-left, plus a size. */
export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The area the panel is positioned within — the app frame, not the canvas's drawing buffer. */
export interface Frame {
  width: number;
  height: number;
}

function clamp(value: number, [lo, hi]: [number, number]): number {
  return Math.min(hi, Math.max(lo, value));
}

/** Where the panel draws, given its anchored offsets and the current frame. */
export function rectFromGeometry(geom: PanelGeometry, frame: Frame): Rect {
  const left = geom.anchor[1] === "l" ? geom.dx : frame.width - geom.dx - geom.width;
  const top = geom.anchor[0] === "t" ? geom.dy : frame.height - geom.dy - geom.height;
  return { left, top, width: geom.width, height: geom.height };
}

/**
 * The offsets to store for a rectangle the user just dropped, anchored to whichever corner it
 * ended up closest to. Rounded to whole pixels: the URL carries this, and sub-pixel drag noise
 * would churn the hash without changing what anyone sees.
 */
export function geometryFromRect(rect: Rect, frame: Frame): PanelGeometry {
  const fromLeft = rect.left;
  const fromRight = frame.width - (rect.left + rect.width);
  const fromTop = rect.top;
  const fromBottom = frame.height - (rect.top + rect.height);

  const horizontal = fromLeft <= fromRight ? "l" : "r";
  const vertical = fromTop <= fromBottom ? "t" : "b";

  return {
    anchor: `${vertical}${horizontal}` as PanelAnchor,
    dx: Math.round(Math.max(0, horizontal === "l" ? fromLeft : fromRight)),
    dy: Math.round(Math.max(0, vertical === "t" ? fromTop : fromBottom)),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
  };
}

/**
 * Pulls a rectangle back inside the frame, shrinking it first if the frame is the smaller of the
 * two. A frame narrower than the panel's minimum width still gets a minimum-width panel that
 * overflows: refusing to draw would be worse than a scrollbar.
 */
export function clampRect(rect: Rect, frame: Frame): Rect {
  const width = clamp(rect.width, [PANEL_WIDTH_RANGE[0], Math.max(PANEL_WIDTH_RANGE[0], Math.min(PANEL_WIDTH_RANGE[1], frame.width))]);
  const height = clamp(rect.height, [PANEL_HEIGHT_RANGE[0], Math.max(PANEL_HEIGHT_RANGE[0], Math.min(PANEL_HEIGHT_RANGE[1], frame.height))]);
  return {
    width,
    height,
    left: clamp(rect.left, [0, Math.max(0, frame.width - width)]),
    top: clamp(rect.top, [0, Math.max(0, frame.height - height)]),
  };
}

/** Field-by-field, so a re-render with an equal-but-new object does not look like a move. */
export function sameGeometry(a: PanelGeometry, b: PanelGeometry): boolean {
  return (
    a.anchor === b.anchor &&
    a.dx === b.dx &&
    a.dy === b.dy &&
    a.width === b.width &&
    a.height === b.height
  );
}
