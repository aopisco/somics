#!/bin/bash
# Stage a list of URLs into s3://somics-dev/raw/<dataset_id>/ with a _manifest.json
# each, from an EC2 box (10x's CDN gives EC2 ~25-30 MB/s against <1 MB/s from a
# laptop). Generic: any dataset whose files are plain HTTP(S) downloads.
#
# Input is the LIST variable: one "<dataset_id>\t<data_access_link>\t<url>" line
# per file. A wrapper user-data sets it and runs this script:
#
#   #!/bin/bash
#   export LIST=$(cat <<'L'
#   tenx_atera_wta_ffpe_human_breast_cancer	https://www.10xgenomics.com/datasets/atera-wta-ffpe-human-breast-cancer	https://cf.10xgenomics.com/samples/atera/dev/X/X_xe_outs.zip
#   L
#   )
#   curl -sL https://raw.githubusercontent.com/aopisco/somics/<branch>/scripts/stage_urls_ec2.sh | bash
#
# The manifest has the same shape as the Visium block's: dataset_id,
# data_access_link, staged_by, files[{key, source_url, bytes, md5, fetched_at}].
# Verify by content, not by status: each download's first bytes are checked
# against the type its name claims (zip / TIFF / text), because a 200 carrying an
# HTML page is the failure that has bitten before.
set -x
exec > /var/log/stage.log 2>&1
B=s3://somics-dev/raw
LOGS=s3://somics-dev/ingest/staging-logs
STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
REGION=us-east-1
: "${LIST:?set LIST}"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
D=/mnt/work; mkdir -p $D && cd $D
finish() { aws s3 cp /var/log/stage.log $LOGS/stage-$STAMP-$1.log --region $REGION; shutdown -h now; }

sniff_ok() {  # $1 file: does the content match the extension?
  local f=$1 m; m=$(head -c 4 "$f" | xxd -p)
  case "$f" in
    *.zip) [ "${m:0:4}" = "504b" ] ;;
    *.tif|*.tiff) [ "${m:0:4}" = "4949" ] || [ "${m:0:4}" = "4d4d" ] ;;
    *.csv|*.html|*.json|*.txt) ! head -c 512 "$f" | grep -qi "<html\|<!doctype" || [[ "$f" == *.html ]] ;;
    *) true ;;
  esac
}

FAILED=0
while IFS=$'\t' read -r KEY LINK URL; do
  [ -z "$KEY" ] && continue
  NAME=$(basename "$URL"); DIR=$D/$KEY; mkdir -p "$DIR"
  echo "### $KEY  $NAME  $(date -u +%FT%TZ)"
  if ! curl -sSL -A "$UA" --retry 8 --retry-all-errors --retry-delay 15 -C - -o "$DIR/$NAME" "$URL"; then echo "FETCH FAILED $URL"; FAILED=1; continue; fi
  if ! sniff_ok "$DIR/$NAME"; then echo "CONTENT MISMATCH $URL ($(head -c 64 "$DIR/$NAME" | tr -d '\n' | cut -c1-64))"; FAILED=1; rm -f "$DIR/$NAME"; continue; fi
  BYTES=$(stat -c %s "$DIR/$NAME"); MD5=$(md5sum "$DIR/$NAME" | cut -d' ' -f1); TS=$(date -u +%FT%TZ)
  aws s3 cp "$DIR/$NAME" "$B/$KEY/$NAME" --region $REGION --only-show-errors || { echo "UPLOAD FAILED $NAME"; FAILED=1; continue; }
  echo "$KEY	$LINK	$NAME	$URL	$BYTES	$MD5	$TS" >> $D/staged.tsv
  rm -f "$DIR/$NAME"
done <<< "$LIST"

# one manifest per dataset, merged with any manifest already there
python3 - "$D/staged.tsv" "$B" <<'PY' || FAILED=1
import csv, json, subprocess, sys
rows = list(csv.reader(open(sys.argv[1]), delimiter="\t")); bucket = sys.argv[2]
by = {}
for key, link, name, url, nbytes, md5, ts in rows:
    by.setdefault(key, {"dataset_id": key, "data_access_link": link, "staged_by": "scripts/stage_urls_ec2.sh", "files": []})
    by[key]["files"].append({"key": name, "source_url": url, "bytes": int(nbytes), "md5": md5, "fetched_at": ts})
for key, m in by.items():
    uri = f"{bucket}/{key}/_manifest.json"
    try:
        prev = json.loads(subprocess.run(["aws", "s3", "cp", uri, "-", "--region", "us-east-1"], capture_output=True, check=True).stdout)
        seen = {f["key"] for f in m["files"]}
        m["files"] = [f for f in prev.get("files", []) if f["key"] not in seen] + m["files"]
    except subprocess.CalledProcessError:
        pass
    subprocess.run(["aws", "s3", "cp", "-", uri, "--region", "us-east-1"], input=json.dumps(m, indent=2).encode(), check=True)
    print("manifest", uri, len(m["files"]), "file(s)")
PY
[ $FAILED -eq 0 ] && finish DONE || finish FAILED
