# Atlas verification, 2026-09-10 -- prefix `ingest/merfish/atlas/2026-09-10T03-01-26Z`

Five EC2 runs of `scripts/verify_visium_ingest.py` via `verify_atlas_ec2.sh`,
each fixing what the previous one taught. Reports live under
`<prefix>/_verify/<stamp>/` (run 1's deleted). **Judge the atlas by runs 3-5.**

| run | stamp | what it established |
|---|---|---|
| 1 | 12-01-11Z (deleted) | "19/19 passed" for **one** Visium section: the shell expanded the spec globs and each per-file run overwrote the family report. `set -f` in the script. |
| 2 | 12-08-53Z | Visium 1408/1416 over 78 sections; the verifier crashed on cell-unit specs (`unit_size_um` absent). Fixed. |
| 3 | 13-31-33Z | Visium 1403/1416 (min px now checked); Xenium 641/684; HuBMAP Xenium 296/320; Atera 16/17. Two failure patterns explained below. |
| 4 | 15-41-03Z | Row sums pass on all 62 cell-unit sections once only gene columns are summed. Atera **18/18**. First registration check (16 cells) too noisy. |
| 5 | 17-3*Z | 200-cell registration check on Xenium + HuBMAP Xenium. |

## Findings

**Visium / Visium HD: rows past the top or left edge of the scan (12 sections,
~7,000 rows of 20.5M).** Space Ranger writes negative pixel positions for spots
and bins outside the microscope image on that side; the builder padded only
bottom/right, so those rows kept negative `x_px`/`y_px`, and the loader slid
their crop boxes to the image edge (`centered_boxes` clips). Expression is
right; the crop shows edge pixels instead of the blank background it should.
Counts per section: CytAssist kidney 1, ovarian 4, sagittal mouse brain 2,
colon post-Xenium 2; HD pancreas 1479, mouse embryo 723, mouse brain 6.5 mm
202, lymph node 1531, colon cancer 2745, ovarian FF 19, pancreatic cancer 171,
mouse kidney 130. **Builder fixed** (pads top/left, shifts positions, records
`pad_offset_px`); the ingested sections stay as they are until the next full
rebuild -- there is no per-section delete path and 0.03% of rows does not
justify a multi-day rebuild on its own.

**Xenium row sums (benign).** `n_counts` is 10x's `transcript_counts` (gene
transcripts); the stored matrix also carries the control and blank codeword
columns. Summing every column overshoots on any section with control counts.
The verifier now sums gene columns only; all 62 cell-unit sections pass.

**"Crops land on tissue" heuristic (14 morphology sections).** Mean intensity
of 128 px crops at the 16 highest-count cells against 16 random windows. On
DAPI in dense tissue (lymph node, tonsil, pancreas) random windows are often as
bright, and the failing sections share their bundle version and image layout
with passing ones. Not a registration signal on morphology images; the check is
kept for H&E, where it is meaningful, and superseded by the registration check
for morphology.

**Registration check (run 4 -> run 5).** Run 4 compared the crop centre with
the crop's own outer ring on 16 uniformly sampled cells: 48 pass, 14 fail with
fractions 0.31-0.56 -- indistinguishable from noise at n = 16 and confounded by
neighbouring nuclei in the ring. Run 5 compares the centre 9x9 with four 9x9
windows 48 px away on 200 cells (registered ~0.85, misregistered ~0.5).

**Tissue label (1).** `Xenium_Prime_Mouse_Pup_FFPE`: spec says "whole
organism", the resolution pass wrote UBERON's "multicellular organism". Same
concept.

**Everything else passes** on every section: presence, tissue, disease state,
obs rows equal to the builders' geometry, technology/unit/organism columns,
positive counts, image row and modality, pixel size agreement, crop shape,
source barcode and feature counts (Visium).
