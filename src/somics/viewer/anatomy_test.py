import math

import pytest

from somics.viewer.anatomy import (
    BODY_BOUNDS,
    ORGANS,
    ORGANS_BY_ID,
    SPECIES,
    Blob,
    Vec3,
    normalize_tissue,
    organ_payload,
    resolve_tissue,
)


def test_node_ids_are_unique():
    assert len(ORGANS_BY_ID) == len(ORGANS)


def test_no_alias_is_claimed_by_two_organs():
    seen: dict[str, str] = {}
    for organ in ORGANS:
        for alias in organ.tissues:
            assert alias not in seen, f"{alias!r} claimed by {seen.get(alias)} and {organ.node_id}"
            seen[alias] = organ.node_id


@pytest.mark.parametrize("species", SPECIES)
def test_every_organ_sits_inside_its_body(species):
    low, high = BODY_BOUNDS[species]
    for organ in ORGANS:
        for blob in organ.blobs(species):
            for axis in range(3):
                assert low[axis] <= blob.center[axis] <= high[axis], (
                    f"{organ.node_id} axis {axis} outside {species} bounds"
                )


@pytest.mark.parametrize("species", SPECIES)
def test_every_organ_is_authored_for_every_body(species):
    """Every body carries all 30 sockets, so a sample can pin onto any of them.

    A fish has no prostate, but the socket is authored anyway at the nearest plausible
    structure — an organ missing for one species would make that sample's pin vanish
    when the body is swapped.
    """
    for organ in ORGANS:
        assert organ.authored(species), f"{organ.node_id} has no {species} geometry"


@pytest.mark.parametrize("species", SPECIES)
def test_no_organ_blob_pokes_out_of_the_body_box(species):
    """Blobs are voxelized inside `BODY_BOUNDS`, so anything outside it is clipped away."""
    low, high = BODY_BOUNDS[species]
    for organ in ORGANS:
        for blob in organ.blobs(species):
            for axis in range(3):
                assert low[axis] <= blob.center[axis] - blob.size[axis], (
                    f"{organ.node_id} axis {axis} runs past the low {species} bound"
                )
                assert blob.center[axis] + blob.size[axis] <= high[axis], (
                    f"{organ.node_id} axis {axis} runs past the high {species} bound"
                )


def test_authored_rejects_unknown_species():
    with pytest.raises(ValueError, match="unknown species"):
        ORGANS[0].authored("axolotl")


@pytest.mark.parametrize("species", SPECIES)
def test_mirrored_organs_come_in_pairs(species):
    for organ in ORGANS:
        if organ.mirror:
            assert len(organ.blobs(species)) == 2 * len(organ.authored(species))


@pytest.mark.parametrize("species", SPECIES)
def test_every_organ_claims_voxels(species):
    """No organ may be wholly swallowed by an earlier one.

    Follows the frontend voxelizer's rule — sample cell centres, assign each to the first
    organ whose ellipsoids contain it — but on a deliberately coarser grid than the one
    that ships. The frontend runs at `VOXEL` = 0.12 (viewer/src/theme.ts); 0.26 here is
    both a safety margin (an organ that survives the coarse grid survives the fine one)
    and a concession to pure-Python speed, since the cost is cubic in 1 / voxel.

    An organ that claims nothing renders as nothing, which is how the human eye vanished
    inside the brain blob.
    """
    voxel = 0.26
    low, high = BODY_BOUNDS[species]
    blobs_by_organ = [(organ.node_id, organ.blobs(species)) for organ in ORGANS]
    claimed = dict.fromkeys((organ.node_id for organ in ORGANS), 0)

    steps = [math.ceil((high[axis] - low[axis]) / voxel) for axis in range(3)]
    for i in range(steps[0]):
        x = low[0] + (i + 0.5) * voxel
        for j in range(steps[1]):
            y = low[1] + (j + 0.5) * voxel
            for k in range(steps[2]):
                point = (x, y, low[2] + (k + 0.5) * voxel)
                for node_id, blobs in blobs_by_organ:
                    if any(_inside(point, blob) for blob in blobs):
                        claimed[node_id] += 1
                        break

    starved = sorted(node_id for node_id, count in claimed.items() if count == 0)
    assert not starved, f"{species}: these organs claim no voxels and will not render: {starved}"


def _inside(point: Vec3, blob: Blob) -> bool:
    return sum(((point[i] - blob.center[i]) / blob.size[i]) ** 2 for i in range(3)) <= 1.0


def test_atlas_v0_tissue_resolves():
    assert resolve_tissue("colon") == "colon"


@pytest.mark.parametrize(
    ("tissue", "expected"),
    [
        ("brain", "brain"),
        ("dorsolateral prefrontal cortex", "brain"),
        ("dorsolateral prefrontal cortex (dlpfc)", "brain"),
        ("brain (nervous tissue)", "brain"),
        ("hippocampus", "brain"),
        ("cerebellum", "brain"),
        ("hypothalamic preoptic region", "brain"),
        ("olfactory bulb", "olfactory_bulb"),
        ("liver", "liver"),
        ("liver (tumor)", "liver"),
        ("breast", "mammary_gland"),
        ("breast cancer", "mammary_gland"),
        ("lung", "lung"),
        ("kidney", "kidney"),
        ("lymph node", "lymph_node"),
        ("pancreas", "pancreas"),
        ("heart", "heart"),
        ("ovary", "ovary"),
        ("tonsil", "tonsil"),
        ("skin", "skin"),
        ("skin (melanoma)", "skin"),
        ("colorectal", "colon"),
        ("colorectal cancer", "colon"),
        ("small intestine", "small_intestine"),
        ("intestine", "colon"),
        ("spleen", "spleen"),
        ("thymus", "thymus"),
        ("testis", "testis"),
        ("prostate", "prostate"),
        ("bone marrow (femur)", "bone_marrow"),
        ("spinal cord", "spinal_cord"),
        ("embryo", "placenta"),
        ("embryonic brain", "placenta"),
        ("peripheral blood mononuclear cells", "blood"),
        ("minor salivary glands", "tongue"),
        ("oral squamous cell carcinoma", "tongue"),
        ("gastric cancer", "stomach"),
        # Fish respiratory anatomy routes to the lung socket; nothing else fits it.
        ("gill", "lung"),
        ("gills", "lung"),
        ("swim bladder", "lung"),
        # ...without stealing the urinary bladder's own labels.
        ("bladder", "bladder"),
        ("urinary bladder", "bladder"),
    ],
)
def test_literature_tissue_labels_route_to_an_organ(tissue, expected):
    assert resolve_tissue(tissue) == expected


@pytest.mark.parametrize("tissue", ["", None, "various", "hela cell line", "leaf", "unknown"])
def test_unplaceable_tissues_resolve_to_none(tissue):
    assert resolve_tissue(tissue) is None


def test_normalize_strips_parentheticals_and_punctuation():
    assert normalize_tissue("Bone Marrow (femur)") == "bone marrow"
    assert normalize_tissue("brain, coronal-section") == "brain coronal section"


@pytest.mark.parametrize("species", SPECIES)
def test_payload_is_json_ready(species):
    payload = organ_payload(species)
    assert len(payload) == len(ORGANS)
    for node in payload:
        assert set(node) == {"node_id", "label", "system", "color", "anchor", "blobs"}
        assert len(node["anchor"]) == 3
        assert node["blobs"]
        assert node["color"].startswith("#")


def test_payload_rejects_unknown_species():
    with pytest.raises(ValueError, match="unknown species"):
        organ_payload("axolotl")
