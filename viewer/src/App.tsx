import { Canvas } from "@react-three/fiber";
import { Suspense, useEffect } from "react";

import { useAgentChannel } from "./agent/useAgentChannel";
import { AgentBanner } from "./agent/AgentBanner";
import { Body } from "./body/Body";
import { CameraRig } from "./camera/CameraRig";
import { layerOpacity } from "./camera/lod";
import { CropTilesLayer } from "./layers/CropTiles";
import { PointCloudLayer } from "./layers/PointCloud";
import { SampleMarkers } from "./markers/SampleMarkers";
import { Panel } from "./panel/Panel";
import { GrassField } from "./scene/GrassField";
import { Motes } from "./scene/Motes";
import { SkyDome } from "./scene/Sky";
import {
  selectCurrentSample,
  selectOrgan,
  useStore,
  viewerState,
} from "./state";
import { SKY } from "./theme";
import { BUDGET_RANGE, PIXEL_RANGE, SPECIES } from "./types";
import type { Species } from "./types";
import { encodeState, useUrlSync } from "./url/urlState";
import { loadingLine } from "./whimsy/loadingLines";
import { useSound } from "./whimsy/useSound";

export function App() {
  useUrlSync();
  useAgentChannel();

  const loadCatalog = useStore((s) => s.loadCatalog);
  const catalogPhase = useStore((s) => s.catalogPhase);
  const catalogError = useStore((s) => s.catalogError);
  const pixel = useStore((s) => s.pixel);
  const lod = useStore((s) => s.lod);
  const species = useStore((s) => s.species);
  const flyRequest = useStore((s) => s.flyRequest);
  const sound = useSound();

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  const zoomOut = useStore((s) => s.zoomOut);
  useEffect(() => {
    if (flyRequest > 0) sound.play(lod === "orbit" ? "squeak" : "whoosh");
    // Deliberately keyed on flyRequest alone: one sound per navigation, not per lod read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flyRequest]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") zoomOut();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomOut]);

  if (catalogPhase === "error") {
    return (
      <div className="fatal">
        <strong>The atlas did not answer.</strong>
        <span>Start the API with `uv run python -m somics.viewer`, then reload.</span>
        <code>{catalogError}</code>
      </div>
    );
  }

  const opacity = layerOpacity(lod);

  return (
    <div className="app">
      <div className="stage">
        <Canvas
          dpr={pixel}
          gl={{ antialias: false, alpha: false }}
          camera={{ fov: 42, position: [0, 8, 34], near: 0.05, far: 4000 }}
        >
          <color attach="background" args={[SKY.horizon]} />
          {/* Scoped so only the sky waits on the panorama: Canvas's own boundary would
              block the whole scene, and the clear colour stands in meanwhile. */}
          <Suspense fallback={null}>
            <SkyDome fade={opacity.world} />
          </Suspense>
          <GrassField fade={opacity.world} species={species} />
          <Motes fade={opacity.world} />
          <Body fade={opacity.body} />
          <SampleMarkers fade={opacity.body} />
          <PointCloudLayer opacity={opacity.points} />
          <CropTilesLayer opacity={opacity.crops} />
          <CameraRig />
        </Canvas>
        <Hud />
      </div>
      <Panel />
    </div>
  );
}

const SPECIES_CRUMB: Record<Species, string> = {
  rat: "🐀 Rat",
  human: "🧍 Human",
  zebrafish: "🐟 Zebrafish",
};

function Hud() {
  const store = useStore();
  const organ = selectOrgan(store, store.node);
  const sample = selectCurrentSample(store);

  return (
    <div className="hud">
      <div className="crumbs">
        <button onClick={() => store.selectNode(null)}>{SPECIES_CRUMB[store.species]}</button>
        {organ && (
          <>
            <span className="sep">›</span>
            <button onClick={() => store.selectSample(null)}>{organ.label}</button>
          </>
        )}
        {sample && (
          <>
            <span className="sep">›</span>
            <button onClick={() => store.setLod("section")}>{sample.section_id}</button>
          </>
        )}
        {store.lod === "cell" && (
          <>
            <span className="sep">›</span>
            <span>morphology</span>
          </>
        )}
      </div>

      <AgentBanner />
      <Toast />

      <div className="controls">
        {SPECIES.map((species) => (
          <button
            key={species}
            className="chip"
            data-active={store.species === species}
            onClick={() => store.setSpecies(species)}
            title="Swap the body. Samples pin to the matching organ whichever one is showing."
          >
            {species}
          </button>
        ))}

        <label className="chip" title="Render scale — lower is chunkier pixels">
          pixels
          <input
            type="range"
            min={PIXEL_RANGE[0]}
            max={PIXEL_RANGE[1]}
            step={0.05}
            value={store.pixel}
            onChange={(event) => store.setPixel(Number(event.target.value))}
          />
        </label>

        {store.sample && (
          <label className="chip" title="Cells fetched from the atlas">
            cells
            <input
              type="range"
              min={BUDGET_RANGE[0]}
              max={Math.min(BUDGET_RANGE[1], sample?.n_cells ?? BUDGET_RANGE[1])}
              step={1000}
              value={store.budget}
              onChange={(event) => store.setBudget(Number(event.target.value))}
            />
            {store.budget.toLocaleString()}
          </label>
        )}

        {store.sample && store.geneList.length > 0 && (
          <label className="chip" title="Colour every cell by one gene's counts">
            gene
            <select
              value={store.gene ?? ""}
              onChange={(event) => store.setGene(event.target.value || null)}
            >
              <option value="">n_counts</option>
              {store.geneList.map((gene) => (
                <option key={gene} value={gene}>
                  {gene}
                </option>
              ))}
            </select>
          </label>
        )}

        <button
          className="chip"
          onClick={() => navigator.clipboard?.writeText(shareUrl(store))}
          title="Copy a link that reopens exactly this view"
        >
          copy link
        </button>

        <button
          className="chip"
          data-active={store.sound}
          onClick={() => store.setSound(!store.sound)}
          title="Squeaks and whooshes"
        >
          {store.sound ? "🔊" : "🔇"}
        </button>
      </div>
    </div>
  );
}

function Toast() {
  const catalogPhase = useStore((s) => s.catalogPhase);
  const pointsPhase = useStore((s) => s.pointsPhase);
  const genePhase = useStore((s) => s.genePhase);
  const gene = useStore((s) => s.gene);
  const budget = useStore((s) => s.budget);

  if (catalogPhase === "loading") return <div className="toast">{loadingLine(0)}</div>;
  if (pointsPhase === "loading") {
    return <div className="toast">{loadingLine(budget)} — pulling cells from the atlas</div>;
  }
  if (genePhase === "loading") {
    return (
      <div className="toast">
        sniffing out <strong>{gene}</strong> across every cell — first read is slow, then cached
      </div>
    );
  }
  if (pointsPhase === "error") return <div className="toast">the atlas dropped that request</div>;
  return null;
}

function shareUrl(state: ReturnType<typeof useStore.getState>): string {
  return `${window.location.origin}${window.location.pathname}${encodeState(viewerState(state))}`;
}
