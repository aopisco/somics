/** Thin client over the viewer API. Binary endpoints carry their metadata in a header. */

import type {
  Anatomy,
  CropTile,
  GeneMeta,
  GeneValues,
  PointCloud,
  PointMeta,
  Sample,
} from "./types";

const META_HEADER = "x-somics-meta";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} -> ${response.status} ${response.statusText}`);
  return (await response.json()) as T;
}

async function getBuffer<M>(path: string): Promise<{ data: ArrayBuffer; meta: M }> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} -> ${response.status} ${response.statusText}`);
  const header = response.headers.get(META_HEADER);
  if (!header) throw new Error(`${path} returned no ${META_HEADER} header`);
  return { data: await response.arrayBuffer(), meta: JSON.parse(header) as M };
}

export const fetchAnatomy = () => getJson<Anatomy>("/api/anatomy");

export const fetchSamples = () => getJson<Sample[]>("/api/samples");

export async function fetchPoints(sectionUid: string, budget: number): Promise<PointCloud> {
  const { data, meta } = await getBuffer<PointMeta>(
    `/api/samples/${sectionUid}/points?max_points=${budget}`,
  );
  const n = meta.n_points;
  return {
    x: new Float32Array(data, 0, n),
    y: new Float32Array(data, 4 * n, n),
    counts: new Float32Array(data, 8 * n, n),
    meta,
  };
}

export async function fetchGeneValues(
  sectionUid: string,
  gene: string,
  budget: number,
): Promise<GeneValues> {
  const { data, meta } = await getBuffer<GeneMeta>(
    `/api/samples/${sectionUid}/genes/${encodeURIComponent(gene)}?max_points=${budget}`,
  );
  return { values: new Float32Array(data, 0, meta.n_points), meta };
}

export const fetchGenes = (sectionUid: string) =>
  getJson<{ genes: string[] }>(`/api/samples/${sectionUid}/genes`).then((body) => body.genes);

export const fetchCrops = (
  sectionUid: string,
  xUm: number,
  yUm: number,
  radiusUm: number,
  limit = 24,
) =>
  getJson<{ tiles: CropTile[] }>(
    `/api/samples/${sectionUid}/crops?x_um=${xUm}&y_um=${yUm}&radius_um=${radiusUm}&limit=${limit}`,
  ).then((body) => body.tiles);
