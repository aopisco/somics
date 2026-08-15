"""Curated organ map that places atlas samples on a rat or human body.

The atlas stores `tissue` as a free-form UBERON *label*, at whatever specificity the
source paper reported ("colon", "dorsolateral prefrontal cortex", "bone marrow
(femur)"). The viewer needs a small fixed set of clickable organs instead, so each
organ carries a list of labels that route to it and `resolve_tissue` does the lookup.

Geometry is authored per species because a rat is a quadruped: for the human the
long axis is +y (feet at y=0) and left/right is x; for the rat the long axis is +x
(nose at +x) and left/right is z. Coordinates are in body units, ~18 tall for the
human and ~18 long for the rat, which the frontend voxelizes at 0.26 units per cube.

Organs are assigned to voxels first-match-wins in `ORGANS` order, so an organ authored
larger than life can silently swallow a smaller neighbour that comes after it — the human
eye disappeared inside an oversized brain blob this way. `test_every_organ_claims_voxels`
pins that.
"""

import re
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

SPECIES = ("human", "rat")

# Index of the left/right axis in each species' body space. Paired organs are
# authored once with a positive lateral coordinate and mirrored across it.
LATERAL_AXIS: dict[str, int] = {"human": 0, "rat": 2}

# Half-extents of each body, for camera framing and voxel grid sizing.
BODY_BOUNDS: dict[str, tuple[Vec3, Vec3]] = {
    "human": ((-4.5, 0.0, -2.5), (4.5, 18.0, 2.5)),
    "rat": ((-9.5, 0.0, -2.5), (9.5, 7.0, 2.5)),
}


@dataclass(frozen=True)
class Blob:
    """One ellipsoid of organ volume: centre and half-extents in body units."""

    center: Vec3
    size: Vec3


@dataclass(frozen=True)
class Organ:
    node_id: str
    label: str
    system: str
    color: str
    tissues: tuple[str, ...]
    human: tuple[Blob, ...]
    rat: tuple[Blob, ...]
    mirror: bool = False

    def blobs(self, species: str) -> tuple[Blob, ...]:
        """Ellipsoids for one species, with paired organs mirrored laterally."""
        authored = self.human if species == "human" else self.rat
        if not self.mirror:
            return authored
        axis = LATERAL_AXIS[species]
        mirrored = []
        for blob in authored:
            center = list(blob.center)
            center[axis] = -center[axis]
            mirrored.append(Blob(tuple(center), blob.size))  # type: ignore[arg-type]
        return authored + tuple(mirrored)

    def anchor(self, species: str) -> Vec3:
        """Where the sample pin floats for this organ."""
        return self.blobs(species)[0].center


ORGANS: tuple[Organ, ...] = (
    Organ(
        node_id="brain",
        label="Brain",
        system="nervous",
        color="#c98bdb",
        tissues=(
            "brain",
            "cerebral cortex",
            "cortex",
            "prefrontal cortex",
            "dorsolateral prefrontal cortex",
            "medial prefrontal cortex",
            "frontal cortex",
            "visual cortex",
            "primary visual cortex",
            "somatosensory cortex",
            "primary motor cortex",
            "motor cortex",
            "middle temporal gyrus",
            "temporal cortex",
            "entorhinal cortex",
            "hippocampus",
            "cerebellum",
            "hypothalamus",
            "hypothalamic preoptic region",
            "thalamus",
            "striatum",
            "amygdala",
            "midbrain",
            "brainstem",
            "substantia nigra",
            "nucleus accumbens",
            "white matter",
            "nervous tissue",
            "neocortex",
        ),
        human=(Blob((0.0, 16.4, -0.05), (1.45, 1.3, 1.3)),),
        rat=(Blob((7.0, 4.9, 0.0), (0.9, 0.7, 0.75)),),
    ),
    Organ(
        node_id="olfactory_bulb",
        label="Olfactory bulb",
        system="nervous",
        color="#b06fd0",
        tissues=("olfactory bulb", "olfactory epithelium", "main olfactory bulb"),
        human=(Blob((0.0, 15.1, 1.15), (0.3, 0.25, 0.4)),),
        rat=(Blob((8.3, 4.7, 0.0), (0.5, 0.4, 0.4)),),
    ),
    Organ(
        node_id="spinal_cord",
        label="Spinal cord",
        system="nervous",
        color="#9d7ad6",
        tissues=("spinal cord", "dorsal root ganglion", "lumbar spinal cord"),
        human=(Blob((0.0, 11.2, -0.85), (0.3, 3.6, 0.3)),),
        rat=(Blob((2.0, 4.9, 0.0), (4.2, 0.28, 0.28)),),
    ),
    Organ(
        node_id="eye",
        label="Eye",
        system="nervous",
        color="#7ec8e3",
        tissues=("eye", "retina", "cornea", "choroid"),
        human=(Blob((0.62, 15.5, 1.3), (0.3, 0.3, 0.3)),),
        rat=(Blob((7.5, 5.0, 0.78), (0.28, 0.28, 0.28)),),
        mirror=True,
    ),
    Organ(
        node_id="tongue",
        label="Tongue and oral cavity",
        system="digestive",
        color="#e8899b",
        tissues=(
            "tongue",
            "oral cavity",
            "oral mucosa",
            "oral squamous cell carcinoma",
            "gingiva",
            "salivary gland",
            "minor salivary glands",
            "parotid gland",
            "submandibular gland",
        ),
        human=(Blob((0.0, 14.7, 0.75), (0.45, 0.3, 0.5)),),
        rat=(Blob((8.3, 3.9, 0.0), (0.5, 0.3, 0.35)),),
    ),
    Organ(
        node_id="thyroid",
        label="Thyroid",
        system="endocrine",
        color="#f0a35e",
        tissues=("thyroid", "thyroid gland", "parathyroid gland"),
        human=(Blob((0.0, 13.9, 0.55), (0.45, 0.25, 0.3)),),
        rat=(Blob((5.7, 4.0, 0.0), (0.3, 0.2, 0.25)),),
    ),
    Organ(
        node_id="tonsil",
        label="Tonsil",
        system="immune",
        color="#f2839a",
        tissues=("tonsil", "palatine tonsil", "adenoid"),
        human=(Blob((0.4, 14.4, 0.6), (0.28, 0.28, 0.28)),),
        rat=(Blob((6.2, 4.2, 0.3), (0.2, 0.2, 0.2)),),
        mirror=True,
    ),
    Organ(
        node_id="thymus",
        label="Thymus",
        system="immune",
        color="#f7b2c2",
        tissues=("thymus",),
        human=(Blob((0.0, 12.4, 0.85), (0.55, 0.5, 0.35)),),
        rat=(Blob((4.4, 4.3, 0.0), (0.4, 0.3, 0.35)),),
    ),
    Organ(
        node_id="lung",
        label="Lung",
        system="respiratory",
        color="#8fd0e8",
        tissues=(
            "lung",
            "lungs",
            "pulmonary",
            "bronchus",
            "trachea",
            "airway",
            "alveolus",
            "lung adenocarcinoma",
            "non-small cell lung cancer",
        ),
        human=(Blob((1.15, 12.1, 0.15), (0.85, 1.7, 0.9)),),
        rat=(Blob((3.4, 4.2, 0.8), (1.2, 0.8, 0.55)),),
        mirror=True,
    ),
    Organ(
        node_id="heart",
        label="Heart",
        system="cardiovascular",
        color="#e8615f",
        tissues=(
            "heart",
            "myocardium",
            "cardiac",
            "left ventricle",
            "right ventricle",
            "atrium",
            "aorta",
            "coronary artery",
        ),
        human=(Blob((-0.35, 12.0, 0.55), (0.85, 1.0, 0.7)),),
        rat=(Blob((3.6, 3.7, 0.0), (0.6, 0.6, 0.6)),),
    ),
    Organ(
        node_id="blood",
        label="Blood",
        system="cardiovascular",
        color="#c0392b",
        tissues=(
            "blood",
            "whole blood",
            "peripheral blood",
            "peripheral blood mononuclear cells",
            "pbmc",
            "buffy coat",
        ),
        human=(Blob((-0.35, 13.0, 0.5), (0.3, 0.3, 0.3)),),
        rat=(Blob((4.3, 3.9, 0.0), (0.25, 0.25, 0.25)),),
    ),
    Organ(
        node_id="liver",
        label="Liver",
        system="digestive",
        color="#a8654a",
        tissues=(
            "liver",
            "hepatic",
            "hepatocellular carcinoma",
            "bile duct",
            "gallbladder",
            "cholangiocarcinoma",
        ),
        human=(Blob((0.95, 10.5, 0.5), (1.9, 0.95, 0.95)),),
        rat=(Blob((2.0, 3.8, 0.0), (1.2, 0.8, 1.0)),),
    ),
    Organ(
        node_id="stomach",
        label="Stomach",
        system="digestive",
        color="#d9a05b",
        tissues=("stomach", "gastric", "gastric cancer", "gastric mucosa", "esophagus"),
        human=(Blob((-0.95, 10.3, 0.4), (1.0, 0.8, 0.6)),),
        rat=(Blob((0.9, 3.3, -0.4), (0.9, 0.7, 0.6)),),
    ),
    Organ(
        node_id="pancreas",
        label="Pancreas",
        system="digestive",
        color="#e6c86e",
        tissues=(
            "pancreas",
            "pancreatic islet",
            "islet of langerhans",
            "pancreatic ductal adenocarcinoma",
        ),
        human=(Blob((-0.2, 9.9, 0.15), (1.2, 0.3, 0.4)),),
        rat=(Blob((0.3, 3.2, 0.2), (0.8, 0.25, 0.4)),),
    ),
    Organ(
        node_id="spleen",
        label="Spleen",
        system="immune",
        color="#8e5c8a",
        tissues=("spleen", "splenic"),
        human=(Blob((-1.55, 10.5, 0.0), (0.5, 0.65, 0.45)),),
        rat=(Blob((0.9, 3.8, 0.95), (0.35, 0.5, 0.3)),),
    ),
    Organ(
        node_id="kidney",
        label="Kidney",
        system="urinary",
        color="#7c9c5a",
        tissues=(
            "kidney",
            "renal",
            "renal cortex",
            "nephron",
            "glomerulus",
            "renal cell carcinoma",
            "ureter",
        ),
        human=(Blob((1.45, 9.8, -0.55), (0.5, 0.7, 0.45)),),
        rat=(Blob((-0.8, 4.1, 0.85), (0.45, 0.55, 0.4)),),
        mirror=True,
    ),
    Organ(
        node_id="adrenal_gland",
        label="Adrenal gland",
        system="endocrine",
        color="#c9a227",
        tissues=("adrenal gland", "adrenal", "adrenal cortex"),
        human=(Blob((1.4, 10.5, -0.5), (0.28, 0.22, 0.28)),),
        rat=(Blob((-0.3, 4.5, 0.8), (0.2, 0.18, 0.2)),),
        mirror=True,
    ),
    Organ(
        node_id="small_intestine",
        label="Small intestine",
        system="digestive",
        color="#e0a86b",
        tissues=(
            "small intestine",
            "duodenum",
            "jejunum",
            "ileum",
            "intestinal villus",
            "gut",
        ),
        human=(Blob((0.0, 8.7, 0.5), (1.5, 1.0, 0.75)),),
        rat=(Blob((-0.7, 2.8, 0.0), (1.6, 0.75, 0.9)),),
    ),
    Organ(
        node_id="colon",
        label="Colon",
        system="digestive",
        color="#d2795b",
        tissues=(
            "colon",
            "large intestine",
            "colorectal",
            "colorectal cancer",
            "colon adenocarcinoma",
            "sigmoid colon",
            "ascending colon",
            "descending colon",
            "transverse colon",
            "rectum",
            "caecum",
            "cecum",
            "appendix",
            "intestine",
        ),
        human=(
            Blob((1.55, 9.0, 0.35), (0.35, 1.1, 0.4)),
            Blob((0.0, 9.9, 0.35), (1.5, 0.35, 0.4)),
            Blob((-1.55, 9.0, 0.35), (0.35, 1.1, 0.4)),
            Blob((0.0, 7.6, 0.3), (0.4, 0.5, 0.4)),
        ),
        rat=(
            Blob((-2.3, 2.9, 0.0), (0.9, 0.4, 0.7)),
            Blob((-1.2, 3.5, 0.0), (0.4, 0.45, 0.6)),
            Blob((-3.2, 2.9, 0.0), (0.4, 0.45, 0.5)),
        ),
    ),
    Organ(
        node_id="bladder",
        label="Bladder",
        system="urinary",
        color="#6fa8a0",
        tissues=("bladder", "urinary bladder", "urothelium"),
        human=(Blob((0.0, 7.2, 0.55), (0.5, 0.45, 0.45)),),
        rat=(Blob((-3.7, 2.6, 0.0), (0.35, 0.32, 0.35)),),
    ),
    Organ(
        node_id="prostate",
        label="Prostate",
        system="reproductive",
        color="#8c7ab8",
        tissues=("prostate", "prostate gland", "prostate cancer"),
        human=(Blob((0.0, 6.8, 0.3), (0.35, 0.3, 0.35)),),
        rat=(Blob((-4.1, 2.7, 0.0), (0.25, 0.22, 0.25)),),
    ),
    Organ(
        node_id="uterus",
        label="Uterus",
        system="reproductive",
        color="#d874a8",
        tissues=("uterus", "endometrium", "myometrium", "cervix", "fallopian tube", "oviduct"),
        human=(Blob((0.0, 7.7, 0.35), (0.5, 0.5, 0.4)),),
        rat=(Blob((-3.4, 3.2, 0.0), (0.4, 0.3, 0.4)),),
    ),
    Organ(
        node_id="ovary",
        label="Ovary",
        system="reproductive",
        color="#e58fbe",
        tissues=("ovary", "ovarian", "ovarian cancer", "corpus luteum"),
        human=(Blob((0.95, 7.9, 0.25), (0.28, 0.25, 0.28)),),
        rat=(Blob((-3.0, 3.5, 0.6), (0.22, 0.2, 0.22)),),
        mirror=True,
    ),
    Organ(
        node_id="testis",
        label="Testis",
        system="reproductive",
        color="#7fb2d8",
        tissues=("testis", "testes", "testicular", "epididymis", "seminiferous tubule"),
        human=(Blob((0.5, 6.2, 0.5), (0.3, 0.35, 0.3)),),
        rat=(Blob((-4.7, 2.0, 0.35), (0.3, 0.3, 0.3)),),
        mirror=True,
    ),
    Organ(
        node_id="placenta",
        label="Placenta and embryo",
        system="reproductive",
        color="#f0b7d4",
        tissues=(
            "placenta",
            "embryo",
            "embryonic",
            "embryonic brain",
            "decidua",
            "chorionic villus",
            "yolk sac",
            "fetus",
            "fetal tissue",
        ),
        human=(Blob((0.0, 8.4, 0.9), (0.75, 0.7, 0.5)),),
        rat=(Blob((-2.9, 3.4, 0.9), (0.5, 0.45, 0.4)),),
    ),
    Organ(
        node_id="mammary_gland",
        label="Breast",
        system="reproductive",
        color="#e896b0",
        tissues=(
            "breast",
            "breast cancer",
            "breast tissue",
            "mammary gland",
            "mammary",
            "nipple",
            "ductal carcinoma in situ",
            "invasive ductal carcinoma",
        ),
        human=(Blob((1.2, 12.2, 1.0), (0.7, 0.6, 0.45)),),
        rat=(Blob((1.0, 2.3, 1.05), (0.5, 0.3, 0.35)),),
        mirror=True,
    ),
    Organ(
        node_id="skin",
        label="Skin",
        system="integumentary",
        color="#e0b48f",
        tissues=(
            "skin",
            "epidermis",
            "dermis",
            "melanoma",
            "hair follicle",
            "cutaneous",
            "squamous cell carcinoma",
        ),
        human=(Blob((0.0, 13.0, 1.15), (1.2, 0.8, 0.18)),),
        rat=(Blob((1.5, 5.3, 0.0), (1.6, 0.18, 0.9)),),
    ),
    Organ(
        node_id="lymph_node",
        label="Lymph node",
        system="immune",
        color="#9fd4b0",
        tissues=(
            "lymph node",
            "lymphoid tissue",
            "germinal centre",
            "germinal center",
            "lymphatic",
        ),
        human=(
            Blob((0.85, 14.0, 0.4), (0.24, 0.24, 0.24)),
            Blob((1.95, 12.5, 0.3), (0.24, 0.24, 0.24)),
            Blob((1.15, 7.3, 0.5), (0.24, 0.24, 0.24)),
        ),
        rat=(
            Blob((5.1, 4.1, 0.55), (0.2, 0.2, 0.2)),
            Blob((2.4, 2.9, 0.95), (0.2, 0.2, 0.2)),
            Blob((-3.4, 2.4, 0.8), (0.2, 0.2, 0.2)),
        ),
        mirror=True,
    ),
    Organ(
        node_id="bone_marrow",
        label="Bone marrow",
        system="skeletal",
        color="#cfd8dc",
        tissues=("bone marrow", "bone", "femur", "marrow", "long bone", "calvaria"),
        human=(Blob((0.95, 4.6, 0.0), (0.28, 1.9, 0.28)),),
        rat=(Blob((-4.3, 1.3, 0.9), (0.22, 0.7, 0.22)),),
        mirror=True,
    ),
    Organ(
        node_id="muscle",
        label="Skeletal muscle",
        system="musculoskeletal",
        color="#c0685f",
        tissues=(
            "skeletal muscle",
            "muscle",
            "quadriceps",
            "gastrocnemius",
            "tibialis anterior",
            "diaphragm",
            "tendon",
        ),
        human=(Blob((1.75, 5.2, 0.4), (0.45, 1.6, 0.45)),),
        rat=(Blob((-4.6, 2.0, 1.0), (0.5, 0.6, 0.35)),),
        mirror=True,
    ),
)

ORGANS_BY_ID = {organ.node_id: organ for organ in ORGANS}

# Longest alias first, so "small intestine" wins over "intestine" and
# "dorsolateral prefrontal cortex" is not shadowed by "cortex".
_ALIASES: list[tuple[str, str]] = sorted(
    ((alias, organ.node_id) for organ in ORGANS for alias in organ.tissues),
    key=lambda pair: -len(pair[0]),
)

_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")

# Values that appear where a tissue should be but name no anatomy.
_UNPLACEABLE = frozenset(
    {
        "",
        "various",
        "multiple",
        "whole body",
        "cell line",
        "organoid",
        "in vitro",
        "unknown",
        "not applicable",
        "na",
    }
)


def normalize_tissue(tissue: str | None) -> str:
    """Lowercase, drop parentheticals and punctuation, collapse whitespace."""
    if not tissue:
        return ""
    text = _PARENTHETICAL.sub(" ", tissue.lower())
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def resolve_tissue(tissue: str | None) -> str | None:
    """Map an atlas tissue label to an organ node id, or None if it names no anatomy.

    Returns
    -------
    str or None
        `node_id` of the matching organ, else None for cell lines, "various",
        and anything the map does not cover.
    """
    text = normalize_tissue(tissue)
    if text in _UNPLACEABLE:
        return None
    if "cell line" in text:
        return None
    for alias, node_id in _ALIASES:
        if text == alias:
            return node_id
    for alias, node_id in _ALIASES:
        if alias in text or (len(text) > 3 and text in alias):
            return node_id
    return None


def organ_payload(species: str) -> list[dict]:
    """Organ nodes as JSON-ready dicts for one species."""
    if species not in SPECIES:
        raise ValueError(f"unknown species {species!r}; expected one of {SPECIES}")
    return [
        {
            "node_id": organ.node_id,
            "label": organ.label,
            "system": organ.system,
            "color": organ.color,
            "anchor": list(organ.anchor(species)),
            "blobs": [
                {"center": list(blob.center), "size": list(blob.size)}
                for blob in organ.blobs(species)
            ],
        }
        for organ in ORGANS
    ]
