#!/bin/bash
# Re-fetch specific HuBMAP files whose staged copies are truncated, verify them,
# and overwrite the objects under s3://somics-dev/hubmap/<HBM-ID>/.
#
# Two of the 20 Xenium z-stacks were short (0.94 GB of ~15 GB; invalid page
# offsets) -- HuBMAP staging incompleteness, not a decode problem. A HuBMAP
# file is fetched from assets.hubmapconsortium.org/<uuid>/<rel_path> (no auth)
# and is complete when its size equals the size the files index records (the
# assets server answers HEAD with 500, so the expected size travels in LIST).
#
# Wrapper user-data:
#   #!/bin/bash
#   export LIST=$(cat <<'L'
#   HBM843.TXKG.335	<uuid>	lab_processed/images/morphology.ome.tiff	16590625903
#   L
#   )
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/<branch>/scripts/restage_hubmap_files_ec2.sh | bash
set -x
exec > /var/log/restage.log 2>&1
: "${LIST:?set LIST}"
REGION=us-east-1; STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ); LOGS=s3://somics-dev/ingest/staging-logs
D=/mnt/work; mkdir -p $D && cd $D
FAILED=0
while IFS=$'\t' read -r HBM UUID REL EXPECT; do
  [ -z "$HBM" ] && continue
  URL="https://assets.hubmapconsortium.org/$UUID/$REL"; OUT="$D/$HBM/$REL"; mkdir -p "$(dirname "$OUT")"
  # assets.hubmapconsortium.org answers some requests with a small error page
  # and status 500 for minutes at a time; only a byte-exact result counts.
  GOT=0
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    curl -sSL -A "Mozilla/5.0 (X11; Linux x86_64) somics-restage" --retry 3 --retry-all-errors --retry-delay 20 -C - -o "$OUT" "$URL" || true
    GOT=$(stat -c %s "$OUT" 2>/dev/null || echo 0)
    if [ -z "$EXPECT" ] || [ "$GOT" = "$EXPECT" ]; then break; fi
    if [ "$GOT" -lt 100000 ]; then rm -f "$OUT"; fi   # an error page, not a partial file
    echo "attempt $attempt: $GOT of $EXPECT bytes; waiting"; sleep 120
  done
  if [ -n "$EXPECT" ] && [ "$GOT" != "$EXPECT" ]; then echo "SHORT $HBM/$REL: $GOT of $EXPECT"; FAILED=1; continue; fi
  aws s3 cp "$OUT" "s3://somics-dev/hubmap/$HBM/$REL" --region $REGION --only-show-errors || { echo "UPLOAD FAILED"; FAILED=1; continue; }
  echo "RESTAGED $HBM/$REL $GOT bytes"; rm -f "$OUT"
done <<< "$LIST"
aws s3 cp /var/log/restage.log $LOGS/restage-$STAMP-$([ $FAILED -eq 0 ] && echo DONE || echo FAILED).log --region $REGION
shutdown -h now
