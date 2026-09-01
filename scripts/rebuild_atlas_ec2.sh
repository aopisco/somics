#!/bin/bash
# Rebuild the whole 59-section atlas from source, on a fresh box, unattended.
#
# Ordered so the artifact survives the machine. The first rebuild was lost to a
# shutdown timer because the atlas lived only on an ephemeral volume and nothing
# copied it off when the run finished -- so here the S3 sync is a pipeline step,
# not a follow-up, and the box terminates on success rather than on a clock.
#
# Run as EC2 user-data. Progress and logs land in s3://somics-dev/rebuild/.
set -x
exec > /var/log/rebuild.log 2>&1

B=s3://somics-dev/rebuild
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
ATLAS_DEST=$B/atlas/$STAMP

# Upload the log, then stop the box. A failed run that keeps running is a bill
# with nothing to show for it, and nobody is necessarily watching.
fail() {
  aws s3 cp /var/log/rebuild.log $B/rebuild-FAILED-$STAMP.log --region us-east-1
  shutdown -h now
  exit 1
}

dnf install -y git unzip tmux
export HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"

# polycomb's pipeline scripts are Claude skills and are not on PyPI.
curl -sSL https://raw.githubusercontent.com/epiblastai/homeobox/refs/heads/main/packages/polycomb/install.sh | bash

D=/mnt/work
# Scratch on the data volume, never /tmp: on AL2023 /tmp is a tmpfs sized to a
# fraction of RAM, so an 18 GB vendor bundle downloaded there fills memory and
# fails with ENOSPC while the 400 GB root sits empty.
TMP=$D/scratch
mkdir -p $D $TMP && cd $D
git clone -b atlas-rebuild https://github.com/aopisco/somics.git repo || fail
cd repo && uv sync || fail
cd $D

# The reference cache, from our mirror rather than Hugging Face: without it
# resolve_genes falls through to gget, which opens MySQL to Ensembl on port 5306
# and hangs forever behind an egress-restricted security group.
aws s3 sync $B/../polycomb/reference_db $D/reference_db --region us-east-1 --only-show-errors || fail
(cd repo && uv run polycomb setup --db-path $D/reference_db) || fail

export SOMICS_DATA_HOME=$D/data
export POLYCOMB_SKILLS=/root/.agents/skills
export SOMICS_SCHEMA=$D/repo/schema/spatial_omics_atlas_schema.yaml
export PYTHON="uv run python"
mkdir -p $SOMICS_DATA_HOME

# ---- fetch + build, one family at a time ---------------------------------
# Ordered fail-fast: each family is fetched immediately before its own build,
# and the lung preview -- the section whose null disease killed the last run --
# goes first. A bad fix now surfaces minutes after boot, not after the full
# ~1.5 h fetch of every other source.
X=$SOMICS_DATA_HOME/datasets/xenium_lung_preview/extracted
MEMBERS=(cells.parquet cell_feature_matrix.h5 morphology_focus.ome.tif experiment.xenium metrics_summary.csv gene_panel.json)
pull_outs() {  # $1 = sample, $2 = url
  local s=$1 u=$2 dir=$X/$1
  mkdir -p "$dir"
  curl -sL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36" -o $TMP/b.zip "$u" || return 1
  unzip -o -j -q $TMP/b.zip "*/cells.parquet" "*/cell_feature_matrix.h5" "*/morphology_focus.ome.tif" \
      "*/experiment.xenium" "*/metrics_summary.csv" "*/gene_panel.json" -d "$dir" \
    || unzip -o -j -q $TMP/b.zip "${MEMBERS[@]}" -d "$dir"
  rm -f $TMP/b.zip
}
CDN=https://cf.10xgenomics.com/samples/xenium
# The non-diseased lung bundle is already in our bucket; 10x's CDN gives us
# ~0.3 MB/s while S3 gives 16, so prefer ours where it exists.
S=Xenium_Preview_Human_Non_diseased_Lung_With_Add_on_FFPE
aws s3 cp s3://somics-dev/raw/human_lung2025_xenium/${S}_outs.zip $TMP/b.zip --region us-east-1 --only-show-errors \
  && mkdir -p $X/$S \
  && (unzip -o -j -q $TMP/b.zip "*/cells.parquet" "*/cell_feature_matrix.h5" "*/morphology_focus.ome.tif" "*/experiment.xenium" "*/metrics_summary.csv" "*/gene_panel.json" -d $X/$S || unzip -o -j -q $TMP/b.zip "${MEMBERS[@]}" -d $X/$S) \
  && rm -f $TMP/b.zip
S2=Xenium_Preview_Human_Lung_Cancer_With_Add_on_2_FFPE
pull_outs $S2 $CDN/1.3.0/$S2/${S2}_outs.zip || fail

cd $D/repo
SPEC=$D/repo/specs/xenium_lung_preview.json bash scripts/run_xenium_pipeline.sh || fail
cd $D

C=Xenium_V1_hColon_Cancer_Add_on_FFPE
mkdir -p $SOMICS_DATA_HOME/datasets/xenium_colon_preview/extracted
X=$SOMICS_DATA_HOME/datasets/xenium_colon_preview/extracted
pull_outs $C $CDN/1.6.0/$C/${C}_outs.zip || fail

cd $D/repo
SPEC=$D/repo/specs/xenium_colon_preview.json bash scripts/run_xenium_pipeline.sh || fail
cd $D

CX=$SOMICS_DATA_HOME/datasets/cosmx_nsclc_ffpe/extracted
mkdir -p $CX && cd $CX
for s in Lung5_Rep1 Lung5_Rep2 Lung5_Rep3 Lung6 Lung9_Rep1 Lung9_Rep2 Lung12 Lung13; do
  mkdir -p "$s"
  aws s3 cp --no-sign-request --region us-west-2 \
    "s3://nanostring-public-share/SMI-Compressed/$s/$s SMI Flat data.tar.gz" $TMP/c.tgz --only-show-errors || fail
  tar -xzf $TMP/c.tgz -C "$s" && rm -f $TMP/c.tgz
  # the tarball nests one level deeper than the builder expects
  [ -d "$s/$s" ] && mv "$s/$s"/* "$s"/ && rmdir "$s/$s"
done

cd $D/repo
bash scripts/run_cosmx_nsclc_pipeline.sh || fail
cd $D

aws s3 cp $B/fetch_zenodo.py $D/fetch_zenodo.py --region us-east-1 || fail
python3 $D/fetch_zenodo.py 10258578 $SOMICS_DATA_HOME/datasets/monkman_nsclc_codex/raw 4 || fail

cd $D/repo
bash scripts/run_monkman_codex_pipeline.sh || fail
bash scripts/run_libd_dlpfc_pipeline.sh    || fail

# ---- preserve the artifact BEFORE anything else can end the machine ------
ATLAS=$SOMICS_DATA_HOME/polycomb_atlases/somics_spatial_atlas
aws s3 sync $ATLAS $ATLAS_DEST --region us-east-1 --only-show-errors || fail
aws s3 cp /var/log/rebuild.log $ATLAS_DEST/_rebuild.log --region us-east-1

# ---- verify, and keep the report next to the atlas ----------------------
uv run python scripts/verify_rebuild_matches_atlas.py --rebuilt $ATLAS > $D/diff.txt 2>&1
aws s3 cp $D/diff.txt $ATLAS_DEST/_verify.txt --region us-east-1
aws s3 cp /var/log/rebuild.log $ATLAS_DEST/_rebuild.log --region us-east-1

shutdown -h now
