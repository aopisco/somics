#!/usr/bin/env python3
"""Write the Stereo-seq builder specs (specs/stereoseq/*.json) for every fetchable source.

What is fetchable, as of 2026-09-17 (docs/2026-09-17_stereoseq_adapter.md):

  * MOSTA (CNGB FTP, public HTTPS): 53 mouse embryo sections E9.5-E16.5, the
    adult mouse brain hemisphere, two olfactory bulb sections -- bin1 GEMs.
  * GEO RAW tarballs already staged under raw/: GSE298650 (mouse heart, 5
    sections, GEM + registered ssDNA TIFF), GSE256319 (mouse brain, one GEF),
    GSE274447 (mouse hippocampus, 3 cellbin GEFs).
  * SpatialGlue's data bundle (staged): 4 Stereo-CITE-seq mouse thymus sections
    (RNA bins + ADT), coordinates in DNB units at bin100.

Not fetchable and not specced: everything on STOmicsDB/CNGB project pages (an
account is needed), the GITomicsDB fish (also non-Ensembl species), the human
GSA rows. GSE267315 is snRNA-seq (mislabelled) and GSE176078 is scRNA-seq.

Run:
    python scripts/make_stereoseq_specs.py
"""

from __future__ import annotations

import json
import os
import re

MOSTA = "https://ftp.cngb.org/pub/SciRAID/stomics/STDS0000058"
S3 = "s3://somics-dev/raw"
MOSTA_EMBRYO_SECTIONS = [
    "E9.5_E1S1", "E9.5_E2S1", "E9.5_E2S2", "E9.5_E2S3", "E9.5_E2S4",
    "E10.5_E1S1", "E10.5_E1S2", "E10.5_E1S3", "E10.5_E2S1",
    "E11.5_E1S1", "E11.5_E1S2", "E11.5_E1S3", "E11.5_E1S4",
    "E12.5_E1S1", "E12.5_E1S2", "E12.5_E1S3", "E12.5_E1S4", "E12.5_E1S5", "E12.5_E2S1",
    "E13.5_E1S1", "E13.5_E1S2", "E13.5_E1S3", "E13.5_E1S4",
    "E14.5_E1S1", "E14.5_E1S2", "E14.5_E1S3", "E14.5_E1S4", "E14.5_E1S5", "E14.5_E2S1", "E14.5_E2S2",
    "E15.5_E1S1", "E15.5_E1S2", "E15.5_E1S3", "E15.5_E1S4", "E15.5_E2S1",
    "E16.5_E1S1", "E16.5_E1S2", "E16.5_E1S3", "E16.5_E1S4", "E16.5_E1S5",
    "E16.5_E2S1", "E16.5_E2S2", "E16.5_E2S3", "E16.5_E2S4", "E16.5_E2S5", "E16.5_E2S6", "E16.5_E2S7",
    "E16.5_E2S8", "E16.5_E2S9", "E16.5_E2S10", "E16.5_E2S11", "E16.5_E2S12", "E16.5_E2S13",
]


def base(key, study, study_name, tissue, link, download_url, notes, *, organism="Mus musculus", life_stage="unknown", preservation="fresh_frozen", disease_state="healthy", bytes_=0, genome="mm10"):
    return {
        "dataset_key": key, "study": study, "study_name": study_name,
        "assay": "Stereo-seq", "technology": "stereo_seq", "spatial_unit": "bin", "segmentation_method": "grid",
        "organism": organism, "tissue": tissue, "preservation": preservation, "life_stage": life_stage,
        "disease_state": disease_state, "disease": None, "image_modality": "dapi",
        "accession_database": "CNGB / STOmicsDB" if "cngb" in link else "GEO",
        "data_access_link": link, "download_url": download_url, "publication_doi": None,
        "source": {"layout": "gem", "dnb_pitch_um": 0.5, "bin_dnb": 20, "genome": genome, "bytes": bytes_, "files": [], "notes": notes},
        "panel": {"panel_name": "Stereo-seq whole transcriptome (poly-A capture)", "vendor": "BGI / STOmics", "technology": "stereo_seq",
                  "organism": organism, "n_targets": None, "has_custom_addon": False,
                  "description": "Untargeted; the feature axis is every gene with at least one MID in the section."},
        "donors": {}, "samples": {},
    }


def mosta_embryo():
    s = base("chen2022_mosta_stereoseq", "MOSTA", "MOSTA: Mouse Organogenesis Spatiotemporal Transcriptomic Atlas (Chen et al. 2022, Cell) -- Stereo-seq embryo sections",
             "embryo", "https://db.cngb.org/stomics/mosta/", f"{MOSTA}/Bin1_matrix/",
             "bin1 GEMs from the MOSTA FTP mirror (public HTTPS). Files are named .tsv.gz but most are plain text; the builder sniffs gzip by magic bytes. "
             "Coordinates are absolute DNB positions (500 nm pitch); bins of 20 DNB = 10 um. No per-section imagery is published. "
             "Section labels: E<stage>_E<embryo>S<section>; one donor per (stage, embryo).", life_stage="embryonic")
    s["publication_doi"] = "10.1016/j.cell.2022.04.003"
    for sec in MOSTA_EMBRYO_SECTIONS:
        stage, rest = sec.split("_")
        embryo = re.match(r"E(\d+)S", rest).group(1)
        donor = f"MOSTA_{stage}_embryo{embryo}"
        s["donors"].setdefault(donor, {"sex": "unknown", "life_stage": "embryonic", "mouse_development_stage": f"embryonic day {stage[1:]}",
                                        "description": f"MOSTA C57BL/6 embryo {embryo} at {stage}; sex not reported."})
        s["samples"][sec] = {"section_id": f"MOSTA_{sec}", "donor_id": donor, "sample_name": f"MOSTA {sec}", "disease_state": "healthy", "disease": None,
                             "anatomical_region": None, "layout": "gem"}
        s["source"]["files"].append({"url": f"{MOSTA}/Bin1_matrix/{sec}_GEM_bin1.tsv.gz", "dest": f"{sec}_GEM_bin1.tsv.gz", "sample": sec, "role": "counts"})
    s["source"]["bytes"] = 60_000_000_000
    return s


def mosta_adult():
    s = base("chen2022_mosta_adult_brain_stereoseq", "MOSTA_adult_brain", "MOSTA: adult mouse brain hemisphere (Chen et al. 2022) -- Stereo-seq bin1",
             "brain", "https://db.cngb.org/stomics/mosta/", f"{MOSTA}/Bin1_matrix/Mouse_brain_Adult_GEM_bin1.tsv.gz",
             "bin1 GEM (gzipped) from the MOSTA FTP mirror. 20 DNB = 10 um bins. The bin60 h5ad of this section on Dropbox (registry rows qiu2022/qiu2024) is the same data and is not ingested separately.",
             life_stage="young_adult", bytes_=470_000_000)
    s["publication_doi"] = "10.1016/j.cell.2022.04.003"
    s["donors"]["MOSTA_adult_mouse"] = {"sex": "unknown", "life_stage": "young_adult", "description": "MOSTA adult C57BL/6 mouse; age and sex not in the release metadata."}
    s["samples"]["Mouse_brain_Adult"] = {"section_id": "MOSTA_Mouse_brain_Adult", "donor_id": "MOSTA_adult_mouse", "sample_name": "MOSTA adult mouse brain hemisphere", "disease_state": "healthy", "disease": None, "layout": "gem"}
    s["source"]["files"].append({"url": f"{MOSTA}/Bin1_matrix/Mouse_brain_Adult_GEM_bin1.tsv.gz", "dest": "Mouse_brain_Adult_GEM_bin1.tsv.gz", "sample": "Mouse_brain_Adult", "role": "counts"})
    return s


def mosta_olfactory():
    s = base("chen2022_stereo", "MOSTA_olfactory_bulb", "MOSTA: adult mouse olfactory bulb (Chen et al. 2022) -- Stereo-seq bin1, two sections",
             "olfactory bulb", "https://db.cngb.org/stomics/mosta/", f"{MOSTA}/Bin1_matrix/",
             "bin1 GEMs from the MOSTA FTP mirror; 20 DNB = 10 um bins. The registry's many olfactory-bulb rows (SEDR tutorial copies) describe this data.",
             life_stage="young_adult", bytes_=1_000_000_000)
    s["publication_doi"] = "10.1016/j.cell.2022.04.003"
    s["donors"]["MOSTA_adult_mouse_olfactory"] = {"sex": "unknown", "life_stage": "young_adult", "description": "MOSTA adult mouse (olfactory bulb sections); age and sex not in the release metadata."}
    for sec in ("Mouse_olfa_S1", "Mouse_olfa_S2"):
        s["samples"][sec] = {"section_id": f"MOSTA_{sec}", "donor_id": "MOSTA_adult_mouse_olfactory", "sample_name": f"MOSTA olfactory bulb {sec[-2:]}", "disease_state": "healthy", "disease": None, "layout": "gem"}
        s["source"]["files"].append({"url": f"{MOSTA}/Bin1_matrix/{sec}_GEM_bin1.tsv.gz", "dest": f"{sec}_GEM_bin1.tsv.gz", "sample": sec, "role": "counts"})
    return s


def geo_heart():
    s = base("stereo_seq_datas_stereo_3", "GSE298650", "Stereo-seq mouse heart, postnatal day 0, 7 and adult (GEO GSE298650)", "heart",
             "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE298650", f"{S3}/stereo_seq_datas_stereo_3/GSE298650_RAW.tar",
             "TissueCut GEMs (tissue-covered DNBs only) + the registered ssDNA TIFF per chip, from the staged RAW tar. 20 DNB = 10 um bins; the TIFF is 1 px = 1 DNB (checked at build: >=95% of bins must fall inside).",
             bytes_=34_000_000_000)
    s["source"]["files"].append({"url": f"{S3}/stereo_seq_datas_stereo_3/GSE298650_RAW.tar", "dest": "GSE298650_RAW.tar"})
    samples = [("GSM9019992", "SS200000171BL_D3", "P0", "sample_P0_p_3"), ("GSM9019993", "SS200000171BL_E3", "P7", "sample_P7_p_1"), ("GSM9019994", "SS200000171BL_E1", "P7", "sample_P7_p_2"),
               ("GSM9019995", "FP200000337BR_B2", "adult", "sample_Adult_B2"), ("GSM9019996", "FP200000337BR_B3", "adult", "sample_Adult_B3")]
    for gsm, chip, age, name in samples:
        donor = f"GSE298650_{name}"
        stage = {"P0": "nursing", "P7": "nursing", "adult": "young_adult"}[age]
        s["donors"][donor] = {"sex": "unknown", "life_stage": stage, "mouse_development_stage": {"P0": "postnatal day 0", "P7": "postnatal day 7", "adult": "adult"}[age], "description": f"GEO {gsm}: mouse heart, {age}."}
        s["samples"][chip] = {"section_id": f"GSE298650_{chip}", "donor_id": donor, "sample_name": f"{gsm} {name}", "disease_state": "healthy", "disease": None, "layout": "gem",
                              "image_description": "Registered ssDNA stain of the chip from the GEO deposit, 1 px = 1 DNB."}
        s["source"]["files"] += [
            {"url": f"{S3}/stereo_seq_datas_stereo_3/GSE298650_RAW.tar", "dest": "GSE298650_RAW.tar", "member": f"{gsm}_{chip}.TissueCut.gem.gz", "sample": chip, "role": "counts"},
            {"url": f"{S3}/stereo_seq_datas_stereo_3/GSE298650_RAW.tar", "dest": "GSE298650_RAW.tar", "member": f"{gsm}_{chip}.tif.gz", "sample": chip, "role": "image"},
        ]
    return s


def geo_brain_gef():
    s = base("stereo_seq_mouse_stereo_2", "GSE256319", "Stereo-seq mouse brain, SAW 3.0.2 GEF (GEO GSE256319)", "brain",
             "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE256319", f"{S3}/stereo_seq_mouse_stereo_2/GSE256319_RAW.tar",
             "bin1 expression from the SAW GEF (HDF5) in the staged RAW tar; 20 DNB = 10 um bins. No registered image in the deposit (the ipr.h5 holds registration parameters only).", bytes_=13_000_000_000)
    s["source"]["files"].append({"url": f"{S3}/stereo_seq_mouse_stereo_2/GSE256319_RAW.tar", "dest": "GSE256319_RAW.tar"})
    s["donors"]["GSE256319_mouse"] = {"sex": "unknown", "life_stage": "unknown", "description": "GEO GSM8093734 StereoSeq Replicate1; donor metadata not in the deposit."}
    s["samples"]["StereoSeq_Replicate1"] = {"section_id": "GSE256319_StereoSeq_Replicate1", "donor_id": "GSE256319_mouse", "sample_name": "GSM8093734 StereoSeq Replicate1", "disease_state": "unknown", "disease": None, "layout": "gef_bin"}
    s["source"]["files"].append({"url": f"{S3}/stereo_seq_mouse_stereo_2/GSE256319_RAW.tar", "dest": "GSE256319_RAW.tar", "member": "GSM8093734_StereoSeq_Replicate1_counts.gef.h5", "sample": "StereoSeq_Replicate1", "role": "counts"})
    s["image_modality"] = None
    return s


def geo_hippocampus_cellbin():
    s = base("stereo_seq_datas2024_stereo", "GSE274447", "Stereo-seq mouse hippocampus, cell-segmented (cellbin) GEFs (GEO GSE274447)", "hippocampus",
             "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE274447", f"{S3}/stereo_seq_datas2024_stereo/GSE274447_RAW.tar",
             "Three adjusted cellbin GEFs (SAW cell segmentation on the ssDNA image) from the staged RAW tar; ingested as cells with the lab's centroids and areas.", bytes_=2_200_000_000)
    s["spatial_unit"] = "cell"; s["segmentation_method"] = "other"; s["image_modality"] = None
    s["source"]["files"].append({"url": f"{S3}/stereo_seq_datas2024_stereo/GSE274447_RAW.tar", "dest": "GSE274447_RAW.tar"})
    for gsm, chip in [("GSM8449354", "C02943C3"), ("GSM9659883", "B03018A2"), ("GSM9659884", "A03599E2")]:
        donor = f"GSE274447_{chip}"
        s["donors"][donor] = {"sex": "unknown", "life_stage": "unknown", "description": f"GEO {gsm}: mouse hippocampus chip {chip}; donor metadata not in the deposit."}
        s["samples"][chip] = {"section_id": f"GSE274447_{chip}", "donor_id": donor, "sample_name": f"{gsm} {chip}", "disease_state": "unknown", "disease": None, "layout": "gef_cellbin", "segmentation_method": "other"}
        s["source"]["files"].append({"url": f"{S3}/stereo_seq_datas2024_stereo/GSE274447_RAW.tar", "dest": "GSE274447_RAW.tar", "member": f"{gsm}_{chip}.adjusted.cellbin.gef", "sample": chip, "role": "counts"})
    return s


def spatialglue_thymus():
    s = base("liao2023_stereo", "SpatialGlue_thymus", "Stereo-CITE-seq mouse thymus, four sections (SpatialGlue data bundle, Long et al. 2024)", "thymus",
             "https://zenodo.org/records/10362607", f"{S3}/liao2023_stereo/Data_SpatialGlue.zip",
             "RNA bins (bin100 = 50 um; obsm['spatial'] in DNB units) plus the ADT protein table on the same bins, from Data_SpatialGlue.zip staged under raw/. "
             "The bundle's spleen datasets are SPOTS (Visium + ADT, 10x barcodes) and are not Stereo-seq; its brain datasets are spatial ATAC/CUT&Tag-RNA. Only the thymus is Stereo-CITE-seq.",
             bytes_=700_000_000)
    s["source"]["layout"] = "h5ad_bins"; s["source"]["bin_dnb"] = 100; s["image_modality"] = None
    s["source"]["files"].append({"url": f"{S3}/liao2023_stereo/Data_SpatialGlue.zip", "dest": "Data_SpatialGlue.zip"})
    for n in (3, 4, 5, 6):
        sample = f"Mouse_Thymus{n - 2}"
        donor = f"SpatialGlue_thymus_mouse{n - 2}"
        s["donors"][donor] = {"sex": "unknown", "life_stage": "unknown", "description": f"SpatialGlue Dataset{n} mouse thymus; donor metadata not in the bundle."}
        s["samples"][sample] = {"section_id": f"SpatialGlue_{sample}", "donor_id": donor, "sample_name": f"SpatialGlue Dataset{n} {sample}", "disease_state": "unknown", "disease": None,
                                "layout": "h5ad_bins", "coord_unit": "dnb", "bin_dnb": 100}
        s["source"]["files"] += [
            {"url": f"{S3}/liao2023_stereo/Data_SpatialGlue.zip", "dest": "Data_SpatialGlue.zip", "member": f"Data_SpatialGlue/Dataset{n}_{sample}/adata_RNA.h5ad", "sample": sample, "role": "counts"},
            {"url": f"{S3}/liao2023_stereo/Data_SpatialGlue.zip", "dest": "Data_SpatialGlue.zip", "member": f"Data_SpatialGlue/Dataset{n}_{sample}/adata_ADT.h5ad", "sample": sample, "role": "protein"},
        ]
    return s


def main() -> None:
    os.makedirs("specs/stereoseq", exist_ok=True)
    for spec in (mosta_embryo(), mosta_adult(), mosta_olfactory(), geo_heart(), geo_brain_gef(), geo_hippocampus_cellbin(), spatialglue_thymus()):
        # de-duplicate the archive entries the loops append per sample (same url + dest, no member)
        seen = set(); files = []
        for f in spec["source"]["files"]:
            k = (f["url"], f["dest"], f.get("member"))
            if k in seen:
                continue
            seen.add(k); files.append(f)
        spec["source"]["files"] = files
        path = f"specs/stereoseq/{spec['dataset_key']}.json"
        json.dump(spec, open(path, "w"), indent=2)
        print(f"wrote {path}: {len(spec['samples'])} section(s), {len(files)} source file(s)")


if __name__ == "__main__":
    main()
