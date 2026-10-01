#!/usr/bin/env python3
"""Literature Xenium deposits on GEO -> one spec per (dataset, panel) in specs/geo_xenium/.

GEO puts a Xenium run's outs files on each sample (GSM) individually, usually
gzipped, sometimes in the older cells.csv.gz + Matrix Market layout or as one
outs tarball, and rarely with experiment.xenium. ``build_xenium_package.py``
reads all of those from a spec's per-sample ``files`` (it decompresses and
converts what it fetches), so this script only has to say which file is which
and add what no deposit states: donor, disease, tissue, panel.

What a GEO record does not say stays null or ``unknown`` (blank beats guessed);
the curation below is per dataset and cites the record field it came from.
A series that mixes gene panels is split into one spec per panel, because a
package carries one PanelSchema row. Perturbed designs are not specced here
(see docs/2026-10-01_literature_xenium.md).

    python3 scripts/make_geo_xenium_specs.py [--only KEY ...] [--no-sizes]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request

OUT_DIR = "specs/geo_xenium"
CACHE = os.environ.get("SOMICS_GEO_CACHE", "/tmp/somics_geo_cache")
UA = {"User-Agent": "curl/8.7.1"}

PANEL_5K = "Xenium Prime 5K Human Pan Tissue and Pathways Panel"
PANEL_MULTI = "Xenium Human Multi-Tissue and Cancer Panel"


def _get(url: str) -> str:
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # GEO throttles bursts; back off and retry
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"GET failed: {url}")


def _head_bytes(url: str) -> int:
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers=UA, method="HEAD")
            with urllib.request.urlopen(req, timeout=60) as r:
                return int(r.headers.get("Content-Length") or 0)
        except Exception:
            time.sleep(3 * (i + 1))
    return 0


def geo_samples(gse: str) -> list[dict]:
    """Every GSM of a series: title, characteristics (dict), supplementary URLs (https)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{gse}.txt")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(_get(f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse}&targ=gsm&form=text&view=brief"))
    samples, cur = [], None
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith("^SAMPLE"):
            cur = {"gsm": line.split("=")[1].strip(), "ch": {}, "supp": [], "title": "", "source": ""}
            samples.append(cur)
        elif cur and line.startswith("!Sample_"):
            k, _, v = line[8:].partition(" = ")
            if k.startswith("characteristics_ch1"):
                ck, _, cv = v.partition(": ")
                cur["ch"][ck.strip()] = cv.strip()
            elif k.startswith("supplementary_file") and v not in ("", "NONE"):
                cur["supp"].append(v.replace("ftp://", "https://"))
            elif k == "title":
                cur["title"] = v
            elif k == "source_name_ch1":
                cur["source"] = v
    return samples


# supplementary-file suffix -> builder ``files`` key (first match wins)
SUFFIX_KEYS = [
    (r"cell_feature_matrix\.h5$", "matrix"),
    (r"(?<!_boundaries\.)cells\.parquet(\.gz)?$", "cells"),
    (r"cells\.csv\.gz$", "cells_csv"),
    (r"matrix\.mtx\.gz$", "matrix_mtx"),
    (r"barcodes\.tsv\.gz$", "barcodes"),
    (r"features\.tsv\.gz$", "features"),
    (r"gene_panel\.json(\.gz)?$", "gene_panel"),
    (r"experiment\.xenium(\.gz)?$", "experiment"),
    (r"morphology_focus_(\d{4})\.ome\.tif(\.gz)?$", "focus_n"),
    (r"morphology_focus\.ome\.tif(\.gz)?$", "focus"),
    (r"(morphology|morpohology)\.ome\.tiff?(\.gz)?$", "zstack"),  # one depositor's spelling
    (r"output\.tar\.gz$", "outs_tar"),
]


def sample_files(urls: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        if "boundaries" in name or "transcripts" in name:
            continue
        for pattern, key in SUFFIX_KEYS:
            m = re.search(pattern, name)
            if not m:
                continue
            if key == "focus_n":
                key = f"focus_{int(m.group(1))}"
            files.setdefault(key, url)
            break
    # the focus projection (or its channel files) is the image; the z-stack is
    # only fetched when there is no focus image, since it is 5-30 GB compressed
    if any(k.startswith("focus") for k in files):
        files.pop("zstack", None)
    return files


def label_from(sample: dict, files: dict) -> str:
    """A legible, stable per-sample label: the depositor's file stem without the GSM."""
    url = files.get("matrix") or files.get("cells") or files.get("cells_csv") or files.get("outs_tar")
    stem = url.rsplit("/", 1)[-1] if url else sample["title"]
    stem = re.sub(r"^GSM\d+_?", "", stem)
    stem = re.sub(r"[-_]?(cell_feature_matrix\.h5|cells\.(parquet|csv)(\.gz)?|xenium_output\.tar\.gz|output\.tar\.gz)$", "", stem)
    return re.sub(r"[^A-Za-z0-9.]+", "_", stem or sample["title"]).strip("_") or sample["gsm"]


def human_donor(desc: str, sex: str = "unknown", diagnosis: str | None = None,
                age: float | None = None, life_stage: str = "unknown") -> dict:
    return {
        "organism": "Homo sapiens",
        "sex": sex,
        "age_value": age,
        "age_unit": "year" if age is not None else None,
        "life_stage": life_stage,
        "human_development_stage": f"{int(age)}-year-old stage" if age is not None else None,
        "ethnicity": None,
        "clinical_diagnosis": diagnosis,
        "description": desc,
    }


def mouse_donor(desc: str, sex: str = "unknown") -> dict:
    return {
        "organism": "Mus musculus",
        "sex": sex,
        "age_value": None,
        "age_unit": None,
        "life_stage": "unknown",
        "mouse_development_stage": None,
        "clinical_diagnosis": None,
        "description": desc,
    }


def _sex(v: str | None) -> str:
    v = (v or "").strip().lower()
    return v if v in ("male", "female") else "unknown"


# ---------------------------------------------------------------- curation
# Each entry: registry dataset_id (dataset_key), the GSE, which GSMs, and a
# function from a GSM record to (donor_key, donor, sample overrides).
# ``tissue``/``disease`` are the record's own words mapped to a resolvable
# term; the record's wording is kept in sample_name.

def bilous(panel_tag: str):
    def pick(s):
        return f"_{panel_tag}_" in label_from(s, sample_files(s["supp"])) + "_"
    def curate(s):
        lab = label_from(s, sample_files(s["supp"]))
        m = re.search(r"_(L\d+|B\d+)_", lab + "_")
        patient = m.group(1)
        lung = patient.startswith("L")
        icd = s["ch"].get("icd-o-3")
        disease = "non-small cell lung carcinoma" if lung else "breast carcinoma"
        return patient, human_donor(
            f"CHUV {'NSCLC' if lung else 'breast cancer'} patient {patient} (GEO GSE311609). "
            "GEO records no age or sex.", diagnosis=disease), {
            "disease_state": "diseased", "disease": disease,
            "sample_name": f"{s['title']}; ICD-O-3: {icd}" if icd else s["title"]}
    return pick, curate


DATASETS = [
    # --- bilous 2026, GSE311609: four gene panels -> four specs
    dict(key="bilous2026_xenium", gse="GSE311609", tissue="lung", preservation="ffpe",
         sel=bilous("5k")[0], curate=bilous("5k")[1], panel=PANEL_5K, segmentation="cell_boundary_stain",
         study_name="Archival human lung tumor FFPE fragments, Xenium Prime 5K (Bilous et al. 2026)"),
    dict(key="bilous2026_xenium_io_custom", gse="GSE311609", tissue="lung", preservation="ffpe",
         sel=bilous("chuvio")[0], curate=bilous("chuvio")[1],
         panel="Xenium custom immuno-oncology panel (340 genes, CHUV)", segmentation="unknown",
         study_name="Archival human lung tumor FFPE fragments, custom immuno-oncology Xenium panel (Bilous et al. 2026)"),
    dict(key="bilous2026_xenium_lung_panel", gse="GSE311609", tissue="lung", preservation="ffpe",
         sel=bilous("lung")[0], curate=bilous("lung")[1], panel="Xenium Human Lung Gene Expression Panel",
         segmentation="unknown",
         study_name="Archival human lung tumor FFPE fragments, Xenium human lung panel (Bilous et al. 2026)"),
    dict(key="bilous2026_xenium_breast", gse="GSE311609", tissue="breast", preservation="ffpe",
         sel=bilous("breast")[0], curate=bilous("breast")[1], panel="Xenium Human Breast Gene Expression Panel",
         segmentation="unknown",
         study_name="Archival human breast tumor FFPE fragments, Xenium human breast panel (Bilous et al. 2026)"),
    # --- IgA nephropathy biopsy, GSE264334 (TopACT)
    dict(key="human_kidney_iga2024_xenium", gse="GSE264334", tissue="kidney", preservation="unknown",
         panel=None, segmentation="unknown",
         study_name="Human IgA nephropathy renal biopsy, Xenium (TopACT, Benjamin et al. 2024)",
         curate=lambda s: ("IgAN", human_donor("IgA nephropathy renal biopsy donor (GEO GSE264334); no age or sex recorded.",
                                                 diagnosis="IgA nephropathy"),
                           {"disease_state": "diseased", "disease": "IgA nephropathy"})),
    # --- mouse brain, GSE342202 (nf_xpatial methods paper; no treatment in the design)
    dict(key="in_house_xenium_2026_xenium", gse="GSE342202", tissue="brain", preservation="fresh_frozen",
         organism="Mus musculus", panel="Xenium mouse brain panel with custom add-on (345 genes, nf_xpatial)",
         segmentation="unknown",
         study_name="Mouse brain, fresh frozen, Xenium (nf_xpatial pipeline paper, 2026)",
         curate=lambda s: (s["title"], mouse_donor(f"Mouse {s['title']} (GEO GSE342202); strain and age not recorded.",
                                                    sex=_sex(s["ch"].get("Sex"))),
                           {"disease_state": "unknown", "disease": None})),
    # --- normal bone marrow clot, GSE322974
    dict(key="normal_bone_marr2026_xenium", gse="GSE322974", tissue="bone marrow", preservation="ffpe",
         panel="Xenium Prime 5K Human Pan Tissue and Pathways Panel with custom add-on", segmentation="unknown",
         study_name="Normal human bone marrow clot biopsy, Xenium 5K (BinarySPA, 2026)",
         curate=lambda s: ("normal_clot", human_donor("Donor of a normal bone marrow clot biopsy (GEO GSE322974); no age or sex recorded."),
                           {"disease_state": "healthy", "disease": None})),
    # --- human amygdala, GSE342289 (nine neurotypical donors; four on Xenium)
    dict(key="xenium2026_xenium", gse="GSE342289", tissue="amygdala", preservation="unknown",
         panel=None, segmentation="unknown",
         study_name="Human amygdala from neurotypical donors, Xenium (2026)",
         curate=lambda s: (s["title"], human_donor(f"Neurotypical brain donor {s['title']} (GEO GSE342289); age and sex not in the record."),
                           {"disease_state": "healthy", "disease": None})),
    # --- non-cancerous human skin, GSE286964 (the registry row says breast cancer; it is skin)
    dict(key="xenium_breast_ca_xenium", gse="GSE286964", tissue="skin of body", preservation="unknown",
         panel=None, segmentation="unknown",
         study_name="Non-cancerous human skin, melanocyte subpopulations, Xenium (2025)",
         curate=lambda s: ("donor", human_donor("63-year-old male skin donor (GEO GSE286964, both blocks).",
                                                 sex="male", age=63.0, life_stage="late_adult"),
                           {"disease_state": "healthy", "disease": None, "sample_name": s["title"]})),
    # --- colorectal liver metastases + adjacent normal, GSE335552: 462- and 319-gene panels
    dict(key="xenium_spatial_t2026_xenium_2", gse="GSE335552", tissue="liver", preservation="ffpe",
         sel=lambda s: s["gsm"] != "GSM9815456", panel=None, segmentation="unknown",
         study_name="Colorectal liver metastases and adjacent normal liver, Xenium before IMC (2026)",
         curate=lambda s: (re.sub(r"[TN]$", "", s["ch"].get("sample identifier", s["title"])),
                           human_donor(f"CRLM patient {re.sub(r'[TN]$', '', s['ch'].get('sample identifier', ''))} (GEO GSE335552).",
                                       diagnosis="colorectal cancer liver metastasis"),
                           ({"disease_state": "diseased", "disease": "colorectal carcinoma"}
                            if "tumor" in s["ch"].get("treatment", "").lower()
                            else {"disease_state": "healthy", "disease": None}))),
    dict(key="xenium_spatial_t2026_xenium_2_319g", gse="GSE335552", tissue="liver", preservation="ffpe",
         sel=lambda s: s["gsm"] == "GSM9815456", panel=None, segmentation="unknown",
         study_name="Colorectal liver metastasis, Xenium before IMC, 319-gene panel (2026)",
         curate=lambda s: (re.sub(r"[TN]$", "", s["ch"].get("sample identifier", s["title"])),
                           human_donor("CRLM patient M397 (GEO GSE335552).", diagnosis="colorectal cancer liver metastasis"),
                           {"disease_state": "diseased", "disease": "colorectal carcinoma"})),
    # --- head and neck tumors with TCR CDR3 probes, GSE300147
    dict(key="xenium_tcr_seq_d_xenium", gse="GSE300147", tissue="oropharynx", preservation="ffpe",
         panel=f"{PANEL_MULTI} plus 100 custom genes (TCR CDR3, HPV)", segmentation="nucleus_expansion",
         study_name="Head and neck squamous cell carcinoma with TCR CDR3 probes, Xenium V1 (2025)",
         curate=lambda s: (
             s["title"].split(",")[0].replace(" ", ""),
             human_donor(f"{s['title'].split(',')[0]} (GEO GSE300147): p16 {s['ch'].get('p16 status')}, "
                         f"{s['ch'].get('recurrence', '').lower()} tumor, {s['ch'].get('pack years')} pack years.",
                         diagnosis="ameloblastoma" if s["source"] == "Ameloblastoma" else "head and neck squamous cell carcinoma"),
             {"disease_state": "diseased",
              "disease": "ameloblastoma" if s["source"] == "Ameloblastoma" else "head and neck squamous cell carcinoma",
              "tissue": {"Oropharynx": "oropharynx", "Oral Cavity": "oral cavity", "Larynx": "larynx",
                         "Hypopharynx": "hypopharynx"}.get(s["ch"].get("site of tumor resection"), "head"),
              "sample_name": s["title"]})),
    # --- myeloma bone marrow trephines, GSE299193 (human; the mouse femur is GSE299195, not specced)
    dict(key="yip2025_xenium", gse="GSE299193", tissue="bone marrow", preservation="ffpe",
         sel=lambda s: s["gsm"] != "GSM9035044", panel="Xenium Human 5K Pan Tissue & Pathways Panel",
         segmentation="cell_boundary_stain",
         study_name="Human bone marrow trephines across MGUS, smouldering and multiple myeloma, Xenium 5K (Yip et al. 2025)",
         curate=lambda s: myeloma(s)),
    dict(key="yip2025_xenium_multitissue", gse="GSE299193", tissue="bone marrow", preservation="ffpe",
         sel=lambda s: s["gsm"] == "GSM9035044", panel=PANEL_MULTI, segmentation="nucleus_expansion",
         study_name="Human bone marrow trephine, Xenium multi-tissue panel (Yip et al. 2025)",
         curate=lambda s: myeloma(s)),
    # --- muscle-invasive bladder cancer, GSE326226
    dict(key="yu2026_xenium", gse="GSE326226", tissue="urinary bladder", preservation="ffpe",
         panel=PANEL_5K, segmentation="unknown",
         study_name="Muscle-invasive bladder cancer, Xenium 5K (Yu et al. 2026)",
         curate=lambda s: (s["title"], human_donor(
             f"Source of MIBC region {s['title']} (GEO GSE326226). GEO does not say which patient each region "
             "came from, so donor is per region.", diagnosis="muscle-invasive bladder carcinoma"),
             {"disease_state": "diseased", "disease": "bladder carcinoma"})),
    # --- COPD lung TMAs, GSE313006: each TMA holds cores from many participants
    dict(key="zhang2026_xenium", gse="GSE313006", tissue="lung", preservation="ffpe",
         panel=None, segmentation="unknown",
         study_name="COPD lung tissue microarrays, custom 480-gene Xenium panel (Zhang et al. 2026)",
         curate=lambda s: (s["title"].split()[-1], {
             **human_donor(f"Cores of {s['title']} (GEO GSE313006): FFPE lung from several of 38 participants "
                           "across the COPD spectrum; cores are not split per participant.", diagnosis="COPD"),
             "sex": "mixed"},
             {"disease_state": "diseased", "disease": "chronic obstructive pulmonary disease"})),
    # --- infant lung TMA (GSE297945): only the 8 samples with a per-sample outs tarball
    dict(key="spatial_transcri2025_xenium", gse="GSE297945", tissue="lung", preservation="ffpe",
         sel=lambda s: any(u.endswith("output.tar.gz") for u in s["supp"]), panel=None, segmentation="unknown",
         study_name="Human infant lungs across development and acute lung injury, Xenium (2025)",
         curate=lambda s: infant(s)),
]


def myeloma(s):
    cond = s["ch"].get("condition")
    disease = {"MM": "multiple myeloma", "RM": "multiple myeloma", "SM": "smoldering multiple myeloma",
               "MGUS": "monoclonal gammopathy of undetermined significance"}.get(cond)
    lab = label_from(s, sample_files(s["supp"]))
    return lab, human_donor(f"Bone marrow trephine donor {lab} (GEO GSE299193), condition {cond}.",
                            sex=_sex(s["ch"].get("Sex")), diagnosis=disease), \
        {"disease_state": "diseased" if disease else "healthy", "disease": disease}


def infant(s):
    donor = s["title"].split("_")[0]
    ch = s["ch"]
    return donor, human_donor(
        f"Infant {donor} (GEO GSE297945): born at {ch.get('age_at_birth')}, lived {ch.get('life_span')}, "
        f"cause of death {ch.get('cause_of_death')}, lung injury score {ch.get('disease_score_dx')}."), \
        {"disease_state": "unknown", "disease": None, "sample_name": s["title"]}


def build_spec(cfg: dict, sizes: bool) -> dict | None:
    gse = cfg["gse"]
    samples = [s for s in geo_samples(gse) if cfg.get("sel", lambda s: True)(s)]
    if not samples:
        return None
    organism = cfg.get("organism", "Homo sapiens")
    out_samples, donors, total = {}, {}, 0
    layouts = set()
    for s in samples:
        files = sample_files(s["supp"])
        if not ({"matrix", "cells"} <= files.keys() or {"matrix", "cells_csv"} <= files.keys()
                or {"matrix_mtx", "cells_csv"} <= files.keys() or "outs_tar" in files):
            print(f"  {cfg['key']}: {s['gsm']} has no usable matrix + cells; skipped ({sorted(files)})")
            continue
        if not any(k.startswith("focus") or k in ("zstack", "outs_tar") for k in files):
            print(f"  {cfg['key']}: {s['gsm']} has no morphology image; skipped")
            continue
        layouts.add("focus_channels" if "focus_1" in files else "focus" if "focus" in files
                    else "tarball" if "outs_tar" in files else "zstack")
        donor_key, donor, over = cfg["curate"](s)
        donor_id = f"{gse}_{donor_key}"
        donors.setdefault(donor_id, donor)
        label = label_from(s, files)
        out_samples[s["gsm"]] = {
            "section_id": f"{gse}_{label}",
            "donor_id": donor_id,
            "sample_name": over.pop("sample_name", s["title"]),
            **over,
            "files": files,
        }
        if sizes:
            total += sum(_head_bytes(u) for u in files.values())
    if not out_samples:
        return None
    multi = layouts == {"focus_channels"}
    return {
        "dataset_key": cfg["key"],
        "study": gse,
        "study_name": cfg["study_name"],
        "assay": "10x Xenium",
        "technology": "xenium",
        "spatial_unit": "cell",
        "segmentation_method": cfg.get("segmentation", "unknown"),
        "organism": organism,
        "tissue": cfg["tissue"],
        "preservation": cfg["preservation"],
        "image_modality": "morphology" if multi else "dapi",
        "accession_database": "GEO",
        "data_access_link": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse}",
        "download_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={sample}",
        "source": {"layout": sorted(layouts), "bytes": total, "n_samples": len(out_samples)},
        "panel": {
            "panel_name": cfg.get("panel"),
            "vendor": "10x Genomics",
            "technology": "xenium",
            "organism": organism,
            "n_targets": None,
            "has_custom_addon": bool(cfg.get("panel") and ("custom" in cfg["panel"].lower() or "plus" in cfg["panel"].lower())),
            "description": None,
        },
        "donors": donors,
        "samples": out_samples,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-sizes", action="store_true", help="skip the HEAD requests for source bytes")
    args = ap.parse_args(argv)
    os.makedirs(OUT_DIR, exist_ok=True)
    for cfg in DATASETS:
        if args.only and cfg["key"] not in args.only:
            continue
        spec = build_spec(cfg, sizes=not args.no_sizes)
        if spec is None:
            print(f"{cfg['key']}: nothing buildable")
            continue
        path = os.path.join(OUT_DIR, f"{cfg['key']}.json")
        with open(path, "w") as fh:
            json.dump(spec, fh, indent=2)
            fh.write("\n")
        print(f"{cfg['key']}: {len(spec['samples'])} samples, {len(spec['donors'])} donors, "
              f"{spec['source']['bytes'] / 1e9:.1f} GB, layout {spec['source']['layout']} -> {path}")


if __name__ == "__main__":
    main()
