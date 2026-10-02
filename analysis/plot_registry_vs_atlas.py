"""What exists against what is in the atlas: species, modality, tissue, disease context.

"Exists" is the registry (data/datasets.csv, is_spatial = yes): one bar per
category, literature and HuBMAP stacked, counted in datasets. "In the atlas" is
the viewer index's samples.json, counted in sections. The units differ, so each
dimension gets two panels with their own scales rather than one shared axis.

The registry's own columns are free text, so they are grouped here by explicit
rules (below). Disease is the weakest: the column is filled for ~5% of rows and
blank for every HuBMAP row, so context is the disease field where present,
otherwise cancer / disease keywords in the dataset name and tissue, otherwise
"unknown"; HuBMAP is its own group (consortium reference donors, mostly without
a diagnosis). Unmatched strings are printed so the rules can be extended.

    uv run --with matplotlib python analysis/plot_registry_vs_atlas.py \\
        --registry data/datasets.csv --samples /tmp/samples.json
"""

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent / "plots" / "registry_vs_atlas.png"

# validated with the dataviz skill's validator (light surface): categorical slots 1-3
LIT, HUB, ATLAS = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#ffffff"

SPECIES_ORDER = ["human", "mouse", "rat", "macaque", "zebrafish", "other species", "several species", "unknown"]


def species_of(text: str) -> str:
    t = text.lower().strip()
    if not t:
        return "unknown"
    if re.search(r"\band\b|,|/|;", t) and len(re.findall(r"human|homo|mouse|mus |murine|rat|macaque", t)) > 1:
        return "several species"
    for pat, name in [(r"human|homo sapiens", "human"), (r"mouse|mus musculus|murine", "mouse"),
                      (r"\brat\b|rattus", "rat"), (r"macaque|macaca|rhesus|cynomolgus", "macaque"),
                      (r"zebrafish|danio", "zebrafish")]:
        if re.search(pat, t):
            return name
    return "other species"


MODALITY_ORDER = ["Sequencing-based capture", "GeoMx (ROI)", "In situ RNA imaging", "Antibody imaging",
                  "MS imaging", "MS proteomics (DVP / LMD)", "Spatial epigenomics", "Histology / autofluorescence",
                  "Other / unknown"]
MODALITY_RULES = [  # first match wins; order matters (DVP before generic MS, HD before Visium...)
    (r"deep visual|\bdvp\b|lmd|lcm|microdissect|nanopots|lc-ms|timstof|orbitrap|mass spectrometry.*proteom|"
     r"proteom.*mass spectrometry|\bdia\b|tmt|timsultra|evosep", "MS proteomics (DVP / LMD)"),
    (r"maldi|desi|\bsims\b|imaging mass spec|\bmsi\b|ims\b|afadesi", "MS imaging"),
    (r"geomx|digital spatial profil", "GeoMx (ROI)"),
    (r"atac|cut&tag|cut-and-tag|chromatin|epigen|methyl|dbit-?seq.*(atac|histone)|spatial-dmt", "Spatial epigenomics"),
    (r"codex|phenocycler|mibi|imaging mass cytometry|\bimc\b|cell ?dive|cycif|ibex|\b4i\b|macsima|seqif|"
     r"multiplex(ed)? (immuno|if|imaging)|immunofluorescence|hyperion|orion|comet|vectra|polaris", "Antibody imaging"),
    (r"xenium|atera|merfish|merscope|cosmx|seqfish|starmap|\biss\b|osmfish|molecular cartography|rnascope|"
     r"hybiss|cartana|eel-?fish|in situ sequencing|smfish|fish\b|resolve|baristaseq|in situ hybridi|molecular imager", "In situ RNA imaging"),
    (r"visium|stereo|slide-?seq|spatial transcriptomics|\bst\b|dbit|hdst|seq-scope|geo-seq|tomo-seq|"
     r"pixel-seq|open-st|bmkmanu|lcm-seq|curio|decoder|cite-seq|sci-space|spatial barcod|spatialtranscriptomics|tomoseq|spatial rna-seq|mux-seq|sptcr|spatial research|distmap", "Sequencing-based capture"),
    (r"histolog|h&e|hematoxylin|auto-?fluorescence|brightfield", "Histology / autofluorescence"),
]


def modality_of(platform: str, modality: str) -> str:
    t = f"{platform} ".lower()
    for pat, name in MODALITY_RULES:
        if re.search(pat, t):
            return name
    m = modality.lower()
    if "epigenom" in m:
        return "Spatial epigenomics"
    return "Other / unknown"


ATLAS_TECH = {"visium": "Sequencing-based capture", "visium_hd": "Sequencing-based capture",
              "stereo_seq": "Sequencing-based capture", "xenium": "In situ RNA imaging", "atera": "In situ RNA imaging",
              "merfish": "In situ RNA imaging", "cosmx": "In situ RNA imaging", "seqfish": "In situ RNA imaging",
              "codex": "Antibody imaging", "phenocycler": "Antibody imaging", "mibi": "Antibody imaging"}

TISSUE_RULES = [
    (r"brain|cortex|cerebr|hippocamp|cerebell|amygdala|striatum|basal gangli|olfactory|spinal|nerve|ganglion|"
     r"neur|hypothal|thalam|midbrain|substantia|dorsolateral", "brain / nervous system"),
    (r"glioma|glioblastoma|medulloblastoma|optic tectum", "brain / nervous system"),
    (r"leaf|leaves|root|seed|stem|flower|silique|shoot", "plant"),
    (r"retina|eye|cornea|ocular", "eye"),
    (r"lung|airway|bronch|trache|alveol|pleur", "lung / airway"),
    (r"heart|cardi|aorta|arter|vascul|vessel|plaque", "heart / vessels"),
    (r"kidney|renal|nephr|bladder|ureter|urinary", "kidney / urinary"),
    (r"liver|hepat|bile|biliar|gallbladder", "liver / biliary"),
    (r"intestin|colon|colorectal|rectum|stomach|gastric|esophag|duoden|ileum|jejun|appendix|gut|bowel|cecum", "gut"),
    (r"pancrea|islet", "pancreas"),
    (r"lymph|spleen|thymus|tonsil|bone marrow|blood|tonsil", "lymphoid / blood"),
    (r"breast|mammary", "breast"),
    (r"placenta|uter|ovar|fallopian|testis|testes|prostate|cervix|endometr|vagin|seminal|oviduct|decidua", "reproductive"),
    (r"skin|derm|epiderm|melanoma", "skin"),
    (r"muscle|bone|cartilage|joint|synovi|tendon|femur", "musculoskeletal"),
    (r"embryo|fetal|whole body|whole organism|pup|organoid", "embryo / whole organism"),
    (r"tumou?r|cancer|carcinoma|tma|tissue microarray", "tumor, unspecified site"),
    (r"head|neck|oral|tongue|saliva|larynx|pharyn|thyroid|adrenal", "head, neck & endocrine"),
]


def tissue_of(text: str) -> str:
    t = text.lower()
    if not t.strip():
        return "unknown"
    for pat, name in TISSUE_RULES:
        if re.search(pat, t):
            return name
    return "other"


CANCER = r"cancer|carcinoma|tumou?r|melanoma|glioma|glioblastoma|lymphoma|leukemi|sarcoma|adenoma|blastoma|myeloma|" \
         r"metasta|neoplas|malignan|mesothelioma|\bdcis\b|\bpdac\b|\bhcc\b|\bnsclc\b|\bccrcc\b|\bhgsoc\b|carcinogen"
DISEASE = r"fibros|covid|sars|alzheimer|dementia|parkinson|\bals\b|sclerosis|diabet|infect|injur|\bipf\b|copd|lupus|" \
          r"arthritis|psoria|colitis|crohn|nephro|nephritis|myocardial|infarct|stroke|hepatitis|steatosis|nafld|nash|" \
          r"asthma|atheroscl|inflamm|disease|syndrome|deficien|dystroph|tuberculosis|malaria|gammopathy|fibrotic|nephropathy"
HEALTHY = r"^(healthy|normal|control|none|non-?diseased|wild[- ]type)$"
DISEASE_ORDER = ["cancer", "other disease", "healthy", "HuBMAP reference donor", "unknown"]


def disease_of_registry(row: dict) -> str:
    if row["dataset_id"].startswith("hubmap_"):
        return "HuBMAP reference donor"
    d = row["disease"].strip().lower()
    if d:
        if re.search(HEALTHY, d):
            return "healthy"
        return "cancer" if re.search(CANCER, d) else "other disease"
    text = f"{row['dataset_name']} {row['tissue']}".lower()
    if re.search(CANCER, text):
        return "cancer"
    if re.search(DISEASE, text):
        return "other disease"
    return "unknown"


def disease_of_atlas(rec: dict) -> str:
    if (rec.get("accession_database") or "").lower().startswith("hubmap"):
        return "HuBMAP reference donor"
    if rec.get("disease_state") == "healthy":
        return "healthy"
    if rec.get("disease_state") == "diseased":
        return "cancer" if re.search(CANCER, (rec.get("disease") or "").lower()) else "other disease"
    return "unknown"


def load(registry: str, samples: str):
    reg = [r for r in csv.DictReader(open(registry)) if r["is_spatial"] == "yes"]
    atl = json.load(open(samples))
    dims = {}
    for name, f_reg, f_atl, order in [
        ("Species", lambda r: species_of(r["species"]), lambda a: species_of(a["organism"] or ""), SPECIES_ORDER),
        ("Modality", lambda r: modality_of(r["platform"], r["modality"]),
         lambda a: ATLAS_TECH.get(a["technology"], "Other / unknown"), MODALITY_ORDER),
        ("Tissue", lambda r: tissue_of(r["tissue"]), lambda a: tissue_of(a["tissue"] or ""), None),
        ("Disease context", disease_of_registry, disease_of_atlas, DISEASE_ORDER),
    ]:
        lit, hub, at = Counter(), Counter(), Counter()
        for r in reg:
            (hub if r["dataset_id"].startswith("hubmap_") else lit)[f_reg(r)] += 1
        for a in atl:
            at[f_atl(a)] += 1
        cats = order or [c for c, _ in (lit + hub + at).most_common()]
        if order is None:  # tissue: top 13 by registry total, the rest folded into "other"
            keep = [c for c, _ in (lit + hub).most_common() if c not in ("other", "unknown")][:13]
            for c in list(lit) + list(hub) + list(at):
                if c not in keep and c not in ("other", "unknown"):
                    lit["other"] += lit.pop(c, 0)
                    hub["other"] += hub.pop(c, 0)
                    at["other"] += at.pop(c, 0)
            cats = keep + ["other", "unknown"]
        cats = [c for c in cats if lit[c] or hub[c] or at[c]]
        dims[name] = (cats, lit, hub, at)
    return reg, atl, dims


def report_unmatched(reg):
    print("platform strings grouped as Other / unknown (top 25):")
    c = Counter(r["platform"] for r in reg if modality_of(r["platform"], r["modality"]) == "Other / unknown")
    for k, v in c.most_common(25):
        print(f"  {v:4d}  {k[:90]}")
    print("tissue strings grouped as other (top 15):")
    c = Counter(r["tissue"] for r in reg if tissue_of(r["tissue"]) == "other")
    for k, v in c.most_common(15):
        print(f"  {v:4d}  {k[:90]}")


def draw(dims, n_reg, n_atl, n_lit, n_hub, out: Path, title: bool = True):
    fig = plt.figure(figsize=(16, 9.2), facecolor=SURFACE)
    blocks = [("Species", 0, 0), ("Modality", 0, 1), ("Tissue", 1, 0), ("Disease context", 1, 1)]
    outer = fig.add_gridspec(2, 2, hspace=0.38, wspace=0.42, left=0.13, right=0.985, top=0.86 if title else 0.83, bottom=0.04,
                             height_ratios=[1, 1.45])
    for name, row, col in blocks:
        cats, lit, hub, at = dims[name]
        inner = outer[row, col].subgridspec(1, 2, wspace=0.08, width_ratios=[1.35, 1])
        ax_r = fig.add_subplot(inner[0])
        ax_a = fig.add_subplot(inner[1], sharey=ax_r)
        y = list(range(len(cats)))[::-1]
        h = 0.68
        lv = [lit[c] for c in cats]
        hv = [hub[c] for c in cats]
        av = [at[c] for c in cats]
        ax_r.barh(y, lv, height=h, color=LIT, edgecolor=SURFACE, linewidth=1.5)
        ax_r.barh(y, hv, left=lv, height=h, color=HUB, edgecolor=SURFACE, linewidth=1.5)
        ax_a.barh(y, av, height=h, color=ATLAS, edgecolor=SURFACE, linewidth=1.5)
        rmax, amax = max(l + hh for l, hh in zip(lv, hv)) or 1, max(av) or 1
        for yi, l, hh in zip(y, lv, hv):
            if l + hh:
                ax_r.text(l + hh + rmax * 0.015, yi, f"{l + hh:,}", va="center", fontsize=8.5, color=INK2)
        for yi, a in zip(y, av):
            ax_a.text(a + amax * 0.03, yi, f"{a:,}" if a else "0", va="center", fontsize=8.5,
                      color=INK2 if a else MUTED)
        ax_r.set_xlim(0, rmax * 1.22)
        ax_a.set_xlim(0, amax * 1.32)
        ax_r.set_yticks(y)
        ax_r.set_yticklabels(cats, fontsize=9.5, color=INK)
        plt.setp(ax_a.get_yticklabels(), visible=False)
        for ax in (ax_r, ax_a):
            ax.tick_params(axis="x", labelsize=8, colors=MUTED, length=0)
            ax.tick_params(axis="y", length=0)
            ax.grid(axis="x", color=GRID, linewidth=0.6)
            ax.set_axisbelow(True)
            for s in ax.spines.values():
                s.set_visible(False)
        ax_r.set_title("exists (datasets)", fontsize=10, color=INK2, loc="left")
        ax_a.set_title("in the atlas (sections)", fontsize=10, color=INK2, loc="left")
        ax_r.text(0, 1.13, name, transform=ax_r.transAxes, fontsize=12.5, color=INK, fontweight="bold")
    if title:
        fig.suptitle("What exists, and what is in the atlas", x=0.13, ha="left", y=0.975, fontsize=17, color=INK,
                     fontweight="bold")
    fig.text(0.13, 0.925 if title else 0.965, f"Registry: {n_reg:,} spatial datasets ({n_lit:,} literature, {n_hub:,} HuBMAP).  "
                          f"Atlas: {n_atl:,} sections.  Different units, so each pair has its own scale.",
             fontsize=10.5, color=INK2)
    # legend: three series, always present
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (LIT, HUB, ATLAS)]
    fig.legend(handles, ["registry: literature", "registry: HuBMAP", "atlas sections"],
               loc="upper right" if title else "upper left",
               bbox_to_anchor=(0.985, 0.985) if title else (0.125, 0.945), ncol=3, frameon=False, fontsize=10)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=200, facecolor=SURFACE)
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default=str(Path(__file__).resolve().parent.parent / "data" / "datasets.csv"))
    ap.add_argument("--samples", required=True, help="viewer index samples.json")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--report", action="store_true", help="print strings the grouping rules did not match")
    ap.add_argument("--no-title", action="store_true", help="omit the figure title (for a slide that has its own)")
    args = ap.parse_args()
    reg, atl, dims = load(args.registry, args.samples)
    if args.report:
        report_unmatched(reg)
    n_hub = sum(r["dataset_id"].startswith("hubmap_") for r in reg)
    draw(dims, len(reg), len(atl), len(reg) - n_hub, n_hub, Path(args.out), title=not args.no_title)
    for name, (cats, lit, hub, at) in dims.items():
        print(f"\n{name}:")
        for c in cats:
            print(f"  {c:32s} literature {lit[c]:5d}  HuBMAP {hub[c]:5d}  atlas {at[c]:5d}")


if __name__ == "__main__":
    main()
