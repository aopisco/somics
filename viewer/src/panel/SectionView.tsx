/**
 * The section's spots or cells, drawn flat in the floating panel.
 *
 * The user asked twice: "i don't want [3d rendered spots] ... i want there to be a panel that
 * appears with the spots on it in 2D". So the measured points are a 2D plot in screen space here,
 * and there is no point cloud in the three.js scene at all. Like the rest of `panel/`, nothing in
 * this file touches `@react-three/*`.
 *
 * Why a `<canvas>` and not SVG or DOM: a Xenium section is 587,115 cells and the budget slider goes
 * to 400,000. That many elements is a non-starter for either — layout and hit-testing alone would
 * cost hundreds of megabytes and seconds per panel resize. Nor is it 400,000 `arc()` calls: this
 * writes an `ImageData` buffer directly and blits it once, which is a tight typed-array loop over a
 * few million bytes and stays interactive while the panel is dragged. The cost of that choice is
 * that a click has to be mapped back by hand — `pixelToUm` in `section.ts`, tested there.
 */

import type { JSX, MouseEvent as ReactMouseEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { LoadPhase } from "../state";
import { useStore } from "../state";
import type { PointCloud } from "../types";
import { formatCount } from "./format";
import type { SectionColors, SectionFit } from "./section";
import {
  buildColors,
  dotRadiusPx,
  fitSection,
  pixelToUm,
  stampOffsets,
  umToPixel,
} from "./section";

/** Behind the plot. Near-black so the dark end of viridis still reads as data, not as background. */
const BACKGROUND: [number, number, number] = [8, 9, 14];
/** Breathing room so dots on the tissue edge are not clipped by the canvas border. */
const PADDING_PX = 6;
/**
 * The plot's shape before the tissue is fitted into it. Wider than tall because the panel is a
 * column: a square plot on a 380px-wide panel pushes all the metadata below the fold.
 */
const ASPECT = 4 / 3;
/** Retina is worth having; past 2x the four-fold pixel cost buys nothing visible. */
const MAX_DPR = 2;
/** The focus crosshair, in CSS pixels. Chrome, not data, so it uses the accent — not a ramp. */
const MARKER_RADIUS = 7;
const MARKER_COLOR = "#ffd479";

/**
 * Paint one frame. Split out of the component because it is the only part that touches a canvas,
 * and because everything it decides — the fit, the colours, the dot size — was decided already.
 */
function draw(
  canvas: HTMLCanvasElement,
  fit: SectionFit,
  dpr: number,
  points: PointCloud,
  colors: SectionColors,
  focusUm: [number, number] | null,
): void {
  const context = canvas.getContext("2d");
  if (!context) return;

  const backingWidth = canvas.width;
  const backingHeight = canvas.height;
  if (backingWidth === 0 || backingHeight === 0) return;

  const image = context.createImageData(backingWidth, backingHeight);
  const data = image.data;
  // Channel by channel rather than a packed 32-bit fill: a Uint32Array view would have to know the
  // machine's endianness, and this costs a couple of milliseconds on a panel-sized canvas.
  for (let i = 0; i < data.length; i += 4) {
    data[i] = BACKGROUND[0];
    data[i + 1] = BACKGROUND[1];
    data[i + 2] = BACKGROUND[2];
    data[i + 3] = 255;
  }

  const stamp = stampOffsets(dotRadiusPx(points.x.length, fit.drawWidth, fit.drawHeight) * dpr);
  const scale = fit.scale * dpr;
  const originX = fit.originX * dpr;
  const originY = fit.originY * dpr;
  const rgb = colors.rgb;

  for (let i = 0; i < points.x.length; i++) {
    // Rounded, not truncated: truncation biases every dot half a pixel up and left, which at the
    // one-pixel dot size a dense Xenium section uses is a visible shear of the whole tissue.
    const cx = Math.round(originX + points.x[i] * scale);
    // Normalized y runs up, canvas y runs down.
    const cy = Math.round(originY - points.y[i] * scale);
    const r = rgb[i * 3];
    const g = rgb[i * 3 + 1];
    const b = rgb[i * 3 + 2];
    for (let s = 0; s < stamp.length; s += 2) {
      const px = cx + stamp[s];
      const py = cy + stamp[s + 1];
      if (px < 0 || py < 0 || px >= backingWidth || py >= backingHeight) continue;
      const offset = (py * backingWidth + px) * 4;
      data[offset] = r;
      data[offset + 1] = g;
      data[offset + 2] = b;
    }
  }

  context.putImageData(image, 0, 0);

  if (!focusUm) return;
  // `putImageData` ignores the transform, so the marker is drawn after it, in CSS pixels.
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  const [mx, my] = umToPixel(fit, focusUm[0], focusUm[1]);
  context.strokeStyle = MARKER_COLOR;
  context.lineWidth = 1.5;
  context.beginPath();
  context.arc(mx, my, MARKER_RADIUS, 0, Math.PI * 2);
  context.moveTo(mx - MARKER_RADIUS * 1.9, my);
  context.lineTo(mx - MARKER_RADIUS * 0.5, my);
  context.moveTo(mx + MARKER_RADIUS * 0.5, my);
  context.lineTo(mx + MARKER_RADIUS * 1.9, my);
  context.stroke();
  context.setTransform(1, 0, 0, 1, 0, 0);
}

export function SectionView({ unit }: { unit: string }): JSX.Element {
  const points = useStore((s) => s.points);
  const phase = useStore((s) => s.pointsPhase);
  const paint = useStore((s) => s.paint);
  const geneValues = useStore((s) => s.geneValues);
  const gene = useStore((s) => s.gene);
  const focusUm = useStore((s) => s.focusUm);
  const setFocusUm = useStore((s) => s.setFocusUm);
  const setLod = useStore((s) => s.setLod);

  const holderRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  /** The plot's CSS size. Measured, not assumed: the panel is resizable. */
  const [box, setBox] = useState({ width: 0, height: 0 });

  // Height is derived from width rather than measured. Measuring both would let the canvas this
  // effect sizes feed back into the box that sized it, and the loop would only be bounded by the
  // rounding happening to agree.
  useEffect(() => {
    const el = holderRef.current;
    if (!el) return;
    const measure = () => {
      const width = el.clientWidth;
      const height = Math.round(width / ASPECT);
      setBox((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const colors = useMemo(
    () => (points ? buildColors(points, paint, geneValues) : null),
    [points, paint, geneValues],
  );
  const fit = useMemo(
    () => (points ? fitSection(points.meta, box.width, box.height, PADDING_PX) : null),
    [points, box.width, box.height],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !points || !colors || !fit || fit.scale <= 0) return;
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    canvas.width = Math.round(fit.width * dpr);
    canvas.height = Math.round(fit.height * dpr);
    draw(canvas, fit, dpr, points, colors, focusUm);
  }, [points, colors, fit, focusUm]);

  function handleClick(event: ReactMouseEvent<HTMLCanvasElement>): void {
    if (!fit || fit.scale <= 0) return;
    // The element's own box, not `offsetX`: the canvas's backing store is a different size from its
    // CSS box, and the bounding rect is the reading that stays right either way.
    const rect = event.currentTarget.getBoundingClientRect();
    setFocusUm(pixelToUm(fit, event.clientX - rect.left, event.clientY - rect.top));
    setLod("cell");
  }

  const drawable = Boolean(points && points.x.length > 0 && fit && fit.scale > 0);
  const label = colors ? rampLabel(colors, gene) : "";

  return (
    <div className="panel-section-view">
      <h2 className="panel-subtitle">{unit}</h2>
      <div className="panel-plot" ref={holderRef}>
        {drawable && points ? (
          <canvas
            ref={canvasRef}
            style={{ width: box.width, height: box.height }}
            onClick={handleClick}
            role="img"
            aria-label={`${formatCount(points.x.length)} ${unit} in their measured positions, coloured by ${label}`}
            title="Click to send the morphology view to that point"
          />
        ) : (
          <PlotPlaceholder phase={phase} />
        )}
      </div>
      {drawable && points && colors && (
        <p className="panel-muted">
          {formatCount(points.x.length)} {unit} · {label} {formatRange(colors.range)}
          {focusUm
            ? ` · looking at (${Math.round(focusUm[0])}, ${Math.round(focusUm[1])}) µm`
            : " · click a point to place the morphology view"}
        </p>
      )}
    </div>
  );
}

function rampLabel(colors: SectionColors, gene: string | null): string {
  return colors.ramp === "magma" ? `${gene ?? "gene"} (magma)` : "transcript count (viridis)";
}

/**
 * Counts are whole and can reach five figures; a gene's mean expression is neither. Rounding the
 * second to "0–3" would hide the range, and `toPrecision` on the first reads as "5.73e+3".
 */
function formatRange([low, high]: [number, number]): string {
  const one = (value: number) =>
    Math.abs(value) >= 100 || Number.isInteger(value)
      ? Math.round(value).toLocaleString("en-US")
      : value.toPrecision(3);
  return `${one(low)}–${one(high)}`;
}

function PlotPlaceholder({ phase }: { phase: LoadPhase }): JSX.Element {
  if (phase === "loading") return <p className="panel-loading">Pulling points from the atlas.</p>;
  if (phase === "error") return <p className="panel-muted">The atlas dropped that point request.</p>;
  return <p className="panel-muted">No points to plot for this section.</p>;
}
