"""Curated organ map that places atlas samples on a human, rat or zebrafish body.

The atlas stores `tissue` as a free-form UBERON *label*, at whatever specificity the
source paper reported ("colon", "dorsolateral prefrontal cortex", "bone marrow
(femur)"). The viewer needs a small fixed set of clickable organs instead, so each
organ carries a list of labels that route to it and `resolve_tissue` does the lookup.

Geometry is authored per species because a rat is a quadruped and a zebrafish swims:
for the human the long axis is +y (feet at y=0) and left/right is x; for the rat and
the zebrafish the long axis is +x (nose at +x) and left/right is z. Coordinates are in
body units — each body is authored to roughly fill its own box, ~18 tall for the human
and ~19 long for the rat and the fish, so the three are *not* to a shared scale. The
frontend voxelizes each at 0.26 units per cube, so authoring the fish at its true size
relative to a rat would leave it a handful of voxels across.

Every organ is authored for every species, even where the species has no such structure
(a fish has no prostate). Each body carries all 30 sockets so a sample can pin onto any
body by homology, which is what the rat already does with the human-only organs; where
the fish has no homologue the blob sits at the nearest plausible structure and says so.

Organs are assigned to voxels first-match-wins in `ORGANS` order, so an organ authored
larger than life can silently swallow a smaller neighbour that comes after it — the human
eye disappeared inside an oversized brain blob this way. `test_every_organ_claims_voxels`
pins that.
"""

import re
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

SPECIES = ("human", "rat", "zebrafish")

# Index of the left/right axis in each species' body space. Paired organs are
# authored once with a positive lateral coordinate and mirrored across it.
LATERAL_AXIS: dict[str, int] = {"human": 0, "rat": 2, "zebrafish": 2}

# Half-extents of each body, for camera framing and voxel grid sizing. The zebrafish
# box starts at y=0 like the others so the camera frames on its midline, but the fish
# itself hovers above y=0 — it is swimming over the field, not lying in it.
BODY_BOUNDS: dict[str, tuple[Vec3, Vec3]] = {
    "human": ((-4.5, 0.0, -2.5), (4.5, 18.0, 2.5)),
    "rat": ((-9.5, 0.0, -2.5), (9.5, 7.0, 2.5)),
    "zebrafish": ((-9.6, 0.0, -1.2), (9.4, 5.4, 1.2)),
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
    zebrafish: tuple[Blob, ...]
    mirror: bool = False

    def authored(self, species: str) -> tuple[Blob, ...]:
        """Ellipsoids as written, before mirroring.

        Raises
        ------
        ValueError
            If `species` is not one of `SPECIES`.
        """
        blobs = {"human": self.human, "rat": self.rat, "zebrafish": self.zebrafish}.get(species)
        if blobs is None:
            raise ValueError(f"unknown species {species!r}; expected one of {SPECIES}")
        return blobs

    def blobs(self, species: str) -> tuple[Blob, ...]:
        """Ellipsoids for one species, with paired organs mirrored laterally."""
        authored = self.authored(species)
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
        zebrafish=(Blob((5.7, 3.3, 0.0), (1.0, 0.52, 0.42)),),
    ),
    Organ(
        node_id="olfactory_bulb",
        label="Olfactory bulb",
        system="nervous",
        color="#b06fd0",
        tissues=("olfactory bulb", "olfactory epithelium", "main olfactory bulb"),
        human=(Blob((0.0, 15.1, 1.15), (0.3, 0.25, 0.4)),),
        rat=(Blob((8.3, 4.7, 0.0), (0.5, 0.4, 0.4)),),
        zebrafish=(Blob((7.35, 2.95, 0.0), (0.34, 0.3, 0.3)),),
    ),
    Organ(
        node_id="spinal_cord",
        label="Spinal cord",
        system="nervous",
        color="#9d7ad6",
        tissues=("spinal cord", "dorsal root ganglion", "lumbar spinal cord"),
        human=(Blob((0.0, 11.2, -0.85), (0.3, 3.6, 0.3)),),
        rat=(Blob((2.0, 4.9, 0.0), (4.2, 0.28, 0.28)),),
        zebrafish=(Blob((0.6, 3.35, 0.0), (5.4, 0.24, 0.24)),),
    ),
    Organ(
        node_id="eye",
        label="Eye",
        system="nervous",
        color="#7ec8e3",
        tissues=("eye", "retina", "cornea", "choroid"),
        human=(Blob((0.62, 15.5, 1.3), (0.3, 0.3, 0.3)),),
        rat=(Blob((7.5, 5.0, 0.78), (0.28, 0.28, 0.28)),),
        # Proportionally the largest organ on the fish: the eye is most of the head.
        zebrafish=(Blob((6.75, 2.95, 0.52), (0.5, 0.5, 0.38)),),
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
        # The fish's basihyal — the tongue-like floor of the mouth — plus oral cavity.
        zebrafish=(Blob((7.6, 2.35, 0.0), (0.5, 0.28, 0.32)),),
    ),
    Organ(
        node_id="thyroid",
        label="Thyroid",
        system="endocrine",
        color="#f0a35e",
        tissues=("thyroid", "thyroid gland", "parathyroid gland"),
        human=(Blob((0.0, 13.9, 0.55), (0.45, 0.25, 0.3)),),
        rat=(Blob((5.7, 4.0, 0.0), (0.3, 0.2, 0.25)),),
        # Teleost thyroid follicles are scattered along the ventral pharyngeal midline
        # rather than gathered into a gland; one blob stands for the whole field.
        zebrafish=(Blob((6.2, 1.95, 0.0), (0.34, 0.24, 0.26)),),
    ),
    Organ(
        node_id="tonsil",
        label="Tonsil",
        system="immune",
        color="#f2839a",
        tissues=("tonsil", "palatine tonsil", "adenoid"),
        human=(Blob((0.4, 14.4, 0.6), (0.28, 0.28, 0.28)),),
        rat=(Blob((6.2, 4.2, 0.3), (0.2, 0.2, 0.2)),),
        # No tonsil in a fish; the nearest thing is the mucosal lymphoid tissue of the
        # pharynx, ventral to the gill arches.
        zebrafish=(Blob((5.9, 1.85, 0.2), (0.24, 0.22, 0.22)),),
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
        # Paired in the fish, dorsal to the gill chamber; authored as one transverse
        # slab because this organ is not mirrored.
        zebrafish=(Blob((5.0, 2.85, 0.0), (0.32, 0.26, 0.5)),),
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
            # The fish breathes with gills; there is no other respiratory socket for a
            # gill sample to land on, and the swim bladder is the lung's homologue.
            "gill",
            "gills",
            "gill arch",
            "swim bladder",
            "gas bladder",
        ),
        human=(Blob((1.15, 12.1, 0.15), (0.85, 1.7, 0.9)),),
        rat=(Blob((3.4, 4.2, 0.8), (1.2, 0.8, 0.55)),),
        # The gill mass behind the eye, one per side.
        zebrafish=(Blob((5.6, 2.55, 0.42), (0.62, 0.62, 0.28)),),
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
        # Two-chambered and far forward: ventral, immediately behind the gills.
        zebrafish=(Blob((4.55, 1.95, 0.0), (0.5, 0.42, 0.4)),),
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
        zebrafish=(Blob((4.95, 2.3, 0.0), (0.26, 0.26, 0.26)),),
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
        # Ventral and left-biased, filling the front of the abdominal cavity.
        zebrafish=(Blob((3.1, 2.05, -0.22), (1.25, 0.5, 0.4)),),
    ),
    Organ(
        node_id="stomach",
        label="Stomach",
        system="digestive",
        color="#d9a05b",
        tissues=("stomach", "gastric", "gastric cancer", "gastric mucosa", "esophagus"),
        human=(Blob((-0.95, 10.3, 0.4), (1.0, 0.8, 0.6)),),
        rat=(Blob((0.9, 3.3, -0.4), (0.9, 0.7, 0.6)),),
        # Zebrafish are stomachless: the intestinal bulb does the stomach's job and
        # takes its socket.
        zebrafish=(Blob((1.9, 1.85, 0.1), (0.85, 0.45, 0.42)),),
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
        # Diffuse along the gut in the fish; the principal islet sits by the bulb, on
        # the right — the gut organs are lateralised, liver left, pancreas right.
        zebrafish=(Blob((1.5, 2.35, 0.3), (0.6, 0.26, 0.3)),),
    ),
    Organ(
        node_id="spleen",
        label="Spleen",
        system="immune",
        color="#8e5c8a",
        tissues=("spleen", "splenic"),
        human=(Blob((-1.55, 10.5, 0.0), (0.5, 0.65, 0.45)),),
        rat=(Blob((0.9, 3.8, 0.95), (0.35, 0.5, 0.3)),),
        # Small and on the left, dorsal to the gut behind the intestinal bulb.
        zebrafish=(Blob((0.9, 2.5, -0.35), (0.42, 0.3, 0.26)),),
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
        # A dorsal ribbon pressed against the body wall under the spine, not a bean.
        zebrafish=(Blob((1.5, 3.05, 0.22), (2.1, 0.28, 0.24)),),
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
        # Fish have no adrenal gland; its steroidogenic tissue is the interrenal,
        # embedded in the anterior kidney. Sits just clear of the kidney ribbon.
        zebrafish=(Blob((3.95, 3.05, 0.2), (0.24, 0.22, 0.22)),),
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
        zebrafish=(Blob((0.1, 1.9, 0.05), (1.25, 0.45, 0.45)),),
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
        # The fish's posterior intestine: a straight run to the vent, no caecum.
        zebrafish=(
            Blob((-1.6, 1.9, 0.0), (0.8, 0.4, 0.38)),
            Blob((-2.5, 1.8, 0.0), (0.42, 0.28, 0.28)),
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
        # A dilation of the urinary ducts by the vent, not a true bladder.
        zebrafish=(Blob((-3.1, 1.95, 0.0), (0.3, 0.26, 0.26)),),
    ),
    Organ(
        node_id="prostate",
        label="Prostate",
        system="reproductive",
        color="#8c7ab8",
        tissues=("prostate", "prostate gland", "prostate cancer"),
        human=(Blob((0.0, 6.8, 0.3), (0.35, 0.3, 0.35)),),
        rat=(Blob((-4.1, 2.7, 0.0), (0.25, 0.22, 0.25)),),
        # No prostate in a fish; the socket sits on the sperm duct behind the vent.
        zebrafish=(Blob((-3.6, 2.15, 0.0), (0.26, 0.24, 0.24)),),
    ),
    Organ(
        node_id="uterus",
        label="Uterus",
        system="reproductive",
        color="#d874a8",
        tissues=("uterus", "endometrium", "myometrium", "cervix", "fallopian tube", "oviduct"),
        human=(Blob((0.0, 7.7, 0.35), (0.5, 0.5, 0.4)),),
        rat=(Blob((-3.4, 3.2, 0.0), (0.4, 0.3, 0.4)),),
        # Zebrafish spawn eggs; the oviduct running to the genital pore takes this
        # socket, since there is no uterus.
        zebrafish=(Blob((-3.0, 2.5, 0.0), (0.3, 0.26, 0.28)),),
    ),
    Organ(
        node_id="ovary",
        label="Ovary",
        system="reproductive",
        color="#e58fbe",
        tissues=("ovary", "ovarian", "ovarian cancer", "corpus luteum"),
        human=(Blob((0.95, 7.9, 0.25), (0.28, 0.25, 0.28)),),
        rat=(Blob((-3.0, 3.5, 0.6), (0.22, 0.2, 0.22)),),
        # A gravid female's ovaries fill much of the abdomen, so this is a big blob.
        zebrafish=(Blob((-1.0, 2.45, 0.32), (0.85, 0.42, 0.3)),),
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
        # Internal and paired, dorsal to the gut in the posterior abdomen.
        zebrafish=(Blob((-2.0, 2.75, 0.28), (0.55, 0.28, 0.24)),),
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
        # Zebrafish embryos develop outside the mother on a yolk sac, so there is no
        # placenta: this socket carries the clutch of eggs waiting at the vent, which
        # is where "embryo" and "yolk sac" samples belong on a fish.
        zebrafish=(Blob((-4.3, 2.4, 0.0), (0.42, 0.32, 0.3)),),
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
        # A fish has no mammary gland and no homologue for one. The socket is kept so
        # breast samples still pin somewhere sane: the ventro-lateral body wall behind
        # the pectoral fin. This one is a stand-in, not anatomy.
        zebrafish=(Blob((3.9, 1.9, 0.35), (0.32, 0.3, 0.22)),),
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
        # Scaled flank along the lateral line, the fish's usual skin sampling site.
        zebrafish=(Blob((0.4, 2.6, 0.62), (1.8, 0.6, 0.22)),),
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
        # Teleosts have no lymph nodes. These stand for the lymphoid aggregates that do
        # the same work, by the gills, along the gut, and behind the vent.
        zebrafish=(
            Blob((4.9, 2.65, 0.35), (0.24, 0.24, 0.24)),
            Blob((2.2, 2.55, 0.4), (0.24, 0.24, 0.24)),
            Blob((-2.2, 2.4, 0.3), (0.24, 0.24, 0.24)),
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
        # Fish have no marrow: haematopoiesis happens in the head kidney, which is
        # where a "bone marrow" sample belongs on a zebrafish.
        zebrafish=(Blob((4.6, 3.0, 0.25), (0.42, 0.3, 0.26)),),
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
        # The myotomes of the caudal peduncle — most of a fish is swimming muscle.
        zebrafish=(Blob((-4.6, 2.6, 0.16), (1.2, 0.45, 0.2)),),
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
