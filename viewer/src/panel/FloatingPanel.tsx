/**
 * The window chrome the spatial information lives in: a DOM overlay in screen space.
 *
 * It is deliberately *not* a `<Html>` from drei or anything else attached to the scene graph. The
 * user asked three times for a 2D panel "locked to the frame, not the 3d viewer": orbiting,
 * zooming or flying between levels must leave it exactly where they put it. Nothing in this file
 * reads the camera, and the element is a sibling of the canvas rather than a child of it, so there
 * is no path by which a camera change could move it.
 *
 * The frame it is locked to is the `.panel-frame` layer below, which fills the stage. Offsets are
 * measured from whichever corner of that layer the panel is nearest (see `geometry.ts`), so a
 * window resize slides it with the edge it was parked against instead of leaving it stranded.
 */

import type { JSX, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { useStore } from "../state";
import { clampRect, geometryFromRect, rectFromGeometry } from "./geometry";
import type { Frame, Rect } from "./geometry";

type DragMode = "move" | "resize";

interface Drag {
  mode: DragMode;
  /** Pointer position when the gesture began, in client coordinates. */
  originX: number;
  originY: number;
  /** The panel's rectangle when the gesture began. */
  rect: Rect;
}

/** Where the panel would be for a pointer at (clientX, clientY), before clamping. */
function dragTo(drag: Drag, clientX: number, clientY: number): Rect {
  const dx = clientX - drag.originX;
  const dy = clientY - drag.originY;
  return drag.mode === "move"
    ? { ...drag.rect, left: drag.rect.left + dx, top: drag.rect.top + dy }
    : { ...drag.rect, width: drag.rect.width + dx, height: drag.rect.height + dy };
}

export function FloatingPanel({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}): JSX.Element {
  const panelOpen = useStore((s) => s.panelOpen);
  const geom = useStore((s) => s.panelGeom);
  const setPanelOpen = useStore((s) => s.setPanelOpen);
  const setPanelGeom = useStore((s) => s.setPanelGeom);

  const frameRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const [frame, setFrame] = useState<Frame>({ width: 0, height: 0 });
  /** The rectangle mid-gesture. Local, so a drag does not write to the URL 60 times a second. */
  const [live, setLive] = useState<Rect | null>(null);

  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const measure = () => setFrame({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const rect = clampRect(live ?? rectFromGeometry(geom, frame), frame);

  const begin = (mode: DragMode) => (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    // The close button lives in the drag handle. Capturing its pointer here would retarget the
    // click to the header, and `preventDefault` below suppresses the compatibility click outright
    // — either way the button goes dead, which is exactly how it broke the first time.
    if (event.target instanceof Element && event.target.closest("button")) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { mode, originX: event.clientX, originY: event.clientY, rect };
    setLive(rect);
  };

  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    setLive(dragTo(drag, event.clientX, event.clientY));
  };

  const end = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    // Recomputed from the event rather than read off `rect`: pointerup can land before React has
    // re-rendered the last pointermove, and committing a stale rectangle would snap the panel back.
    setPanelGeom(geometryFromRect(clampRect(dragTo(drag, event.clientX, event.clientY), frame), frame));
    setLive(null);
  };

  const handlers = { onPointerMove: move, onPointerUp: end, onPointerCancel: end };

  // The frame layer stays mounted while the panel is closed so it keeps measuring: reopening then
  // draws in the right place instead of one frame late.
  return (
    <div className="panel-frame" ref={frameRef}>
      {panelOpen && frame.width > 0 && (
        <section
          className="panel-window"
          style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
          aria-label="Spatial information"
        >
          <header className="panel-window-bar" onPointerDown={begin("move")} {...handlers}>
            <span className="panel-window-title" title={title}>
              {title}
            </span>
            <button
              className="panel-window-close"
              onClick={() => setPanelOpen(false)}
              title="Close this panel — reopen it from the panel control"
              aria-label="Close the spatial information panel"
            >
              ×
            </button>
          </header>

          {children}

          {/* Pointer-only, and hidden from assistive tech: there is no keyboard resize to expose,
              and the panel's size is not information — it is already in the URL either way. */}
          <div
            className="panel-window-grip"
            onPointerDown={begin("resize")}
            {...handlers}
            aria-hidden="true"
          />
        </section>
      )}
    </div>
  );
}
