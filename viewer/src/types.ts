/** Shared contracts between the API, the scene graph, and the URL. */

export type Species = "human" | "rat" | "zebrafish";

/** The union as a value, so validators do not each keep their own hand-typed copy.
 *  Order is the order the body chips appear in. */
export const SPECIES: Species[] = ["rat", "human", "zebrafish"];

/** The emoji+name a body is called by, wherever the UI names one. Lives here rather than in
 *  either component that shows it: the HUD breadcrumb and the panel's back control must read as
 *  one vocabulary, and keeping two copies is what let the zebrafish crumb read "🧍 Human". */
export const SPECIES_CRUMB: Record<Species, string> = {
  rat: "🐀 Rat",
  human: "🧍 Human",
  zebrafish: "🐟 Zebrafish",
};

/** Zoom levels, outermost first. The camera crossfades between them by distance. */
export type Lod = "orbit" | "organ" | "section" | "cell";

export const LODS: Lod[] = ["orbit", "organ", "section", "cell"];

export type Vec3 = [number, number, number];

/** What the point cloud is coloured by. */
export type Paint = "counts" | "gene";

export interface Blob {
  center: Vec3;
  size: Vec3;
}

export interface OrganNode {
  node_id: string;
  label: string;
  system: string;
  color: string;
  anchor: Vec3;
  blobs: Blob[];
}

export interface BodyDef {
  bounds: [Vec3, Vec3];
  organs: OrganNode[];
}

export interface Anatomy {
  species: Species[];
  bodies: Record<Species, BodyDef>;
}

export interface Sample {
  section_uid: string;
  section_id: string;
  dataset_uid: string;
  /** Organ this sample pins to, or null when its tissue names no anatomy. */
  node_id: string | null;
  tissue: string | null;
  organism: string | null;
  species: Species;
  disease: string | null;
  disease_state: string | null;
  preservation: string | null;
  technology: string | null;
  assay: string | null;
  spatial_unit: string | null;
  n_cells: number;
  /** [x_min, y_min, x_max, y_max] in the section's own micron frame. */
  extent_um: [number, number, number, number];
  has_gene_expression: boolean;
  has_morphology_crop: boolean;
  has_he_crop: boolean;
  feature_spaces: string[];
  study_name: string | null;
  sample_name: string | null;
  dataset_description: string | null;
  accession_database: string | null;
  accession_id: string | null;
  data_access_link: string | null;
  donor: Record<string, string | number | boolean>;
  panel: Record<string, string | number | boolean>;
}

export interface PointMeta {
  n_points: number;
  n_cells: number;
  extent_um: [number, number, number, number];
  /** Microns per unit of normalized position; the round trip back to crop coords. */
  scale_um: number;
  count_range: [number, number];
}

/**
 * One section's cells. Positions are normalized to [-1, 1] on the longer axis with
 * aspect preserved; multiply by `meta.scale_um` and re-centre to get microns back.
 */
export interface PointCloud {
  x: Float32Array;
  y: Float32Array;
  counts: Float32Array;
  meta: PointMeta;
}

export interface GeneMeta {
  gene: string;
  n_points: number;
  value_range: [number, number];
  max_observed: number;
}

export interface GeneValues {
  values: Float32Array;
  meta: GeneMeta;
}

/** One image crop, positioned in the section's micron frame. */
export interface CropTile {
  uid: string;
  x_um: number;
  y_um: number;
  width_um: number;
  height_um: number;
  /** base64 PNG: greyscale and percentile-stretched for morphology, colour for H&E. */
  png: string;
}

export interface CameraState {
  position: Vec3;
  target: Vec3;
}

/**
 * Which frame corner the floating panel measures its offsets from: vertical edge first
 * (`t`op / `b`ottom), then horizontal (`l`eft / `r`ight).
 */
export type PanelAnchor = "tl" | "tr" | "bl" | "br";

export const PANEL_ANCHORS: PanelAnchor[] = ["tl", "tr", "bl", "br"];

/**
 * The floating panel's place on the frame, in CSS pixels. Offsets are measured from `anchor`
 * rather than always from the top-left so a panel parked against the right or bottom edge stays
 * against it when the window is resized.
 */
export interface PanelGeometry {
  anchor: PanelAnchor;
  /** Distance from the anchor's horizontal frame edge to the panel's near vertical side. */
  dx: number;
  /** Distance from the anchor's vertical frame edge to the panel's near horizontal side. */
  dy: number;
  width: number;
  height: number;
}

/** Everything the URL round-trips. Any change here needs a matching codec change. */
export interface ViewerState {
  species: Species;
  /** Selected organ node_id, or null. */
  node: string | null;
  /** Selected section_uid, or null. */
  sample: string | null;
  lod: Lod;
  gene: string | null;
  paint: Paint;
  camera: CameraState | null;
  /** Where the cell level is looking, in the section's own micron frame. */
  focusUm: [number, number] | null;
  /** Cells requested from the points endpoint. */
  budget: number;
  /** Render scale for the pixelated look; 1 is full resolution. */
  pixel: number;
  sound: boolean;
  /** Whether the floating spatial-information panel is showing. */
  panelOpen: boolean;
  /** Where that panel sits on the frame. Screen space only — the camera never moves it. */
  panelGeom: PanelGeometry;
}

export const DEFAULT_STATE: ViewerState = {
  species: "rat",
  node: null,
  sample: null,
  lod: "orbit",
  gene: null,
  paint: "counts",
  camera: null,
  focusUm: null,
  budget: 80_000,
  pixel: 0.4,
  sound: false,
  panelOpen: true,
  panelGeom: { anchor: "tr", dx: 24, dy: 24, width: 380, height: 560 },
};

export const BUDGET_RANGE: [number, number] = [1_000, 400_000];
export const PIXEL_RANGE: [number, number] = [0.15, 1];
/** Narrower than this and the metadata rows wrap into unreadable slivers. */
export const PANEL_WIDTH_RANGE: [number, number] = [260, 1600];
/** Shorter than this and the title bar plus one row is all that fits. */
export const PANEL_HEIGHT_RANGE: [number, number] = [160, 1600];
