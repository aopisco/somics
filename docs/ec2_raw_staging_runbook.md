# Staging the raw corpus from EC2

Mirrors the ~318 remaining source bundles (~1.6 TB) into
`s3://somics-dev/raw/<dataset_id>/`. Written for EC2 because throughput from a
laptop was measured at 8–24 MB/s — about two days — against a few hours from
inside us-east-1.

## Why EC2 specifically

- **Same region as the bucket.** `somics-dev` is us-east-1; writes from an
  in-region instance are free and fast.
- **Bandwidth.** The job is entirely network-bound, so the instance is chosen
  for its network, not its CPU or disk.
- **Nothing touches local disk.** The script streams each HTTP response
  straight into a multipart upload, so a 374 GB bundle needs no 374 GB volume.
  The default 8 GB root volume is enough.

## Instance

| | |
|---|---|
| region | **us-east-1** (must match the bucket) |
| type | `m5n.large` or `c5n.large` — network-optimised, ~25 Gbps burst |
| storage | default root volume; no extra EBS |
| IAM | instance profile with S3 write to `somics-dev` (below) |

Spot is fine: the job is resumable, so an interruption costs only the bundle
in flight.

## IAM

Attach an instance profile rather than copying credentials onto the box. The
script uses boto3's default credential chain when `--profile` is omitted.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject",
               "s3:ListBucket", "s3:AbortMultipartUpload"],
    "Resource": ["arn:aws:s3:::somics-dev", "arn:aws:s3:::somics-dev/*"]
  }]
}
```

`GetObject` and `ListBucket` are needed for resume (reading each
`_manifest.json`); `DeleteObject` and `AbortMultipartUpload` let the script
clean up after a rejected or interrupted fetch.

## Run

```bash
sudo dnf install -y git tmux                      # or apt, on Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/aopisco/somics.git && cd somics

tmux new -s staging                               # survives disconnect
uv run --with boto3 python scripts/stage_raw_to_s3.py \
    --bucket somics-dev \
    --max-bytes 20e9 \
    2>&1 | tee ~/staging.log
```

No `--profile`: the instance role is picked up automatically. Detach with
`Ctrl-b d`, reattach with `tmux attach -t staging`.

`--max-bytes 20e9` skips bundles over 20 GB, which excludes the one 374 GB GEO
archive (`GSE147672`, 19% of the total on its own, and likely to contain raw
sequencing rather than spatial data). Drop the flag, or raise it, once that has
been looked at deliberately.

## Resuming

Re-run the identical command. A dataset whose `_manifest.json` records a
completed fetch of the same source URL is skipped, so an interrupted run picks
up where it stopped. There is no separate resume mode and no state file.

## What to expect

From the six-host trial: all six staged cleanly, but two host-specific faults
turned up in six attempts, so expect more across ~300.

- **Roughly 10–15% will fail.** Two source URLs are already dead and 38 are
  behind bot protection. Failures are collected and printed at the end rather
  than stopping the run.
- **HTML instead of data.** Some hosts answer 200 with a landing page. The
  script sniffs for that, deletes the partial object and retries with a plain
  user-agent, because the browser agent needed to get past Cloudflare on 10x is
  exactly what makes Dropbox serve a preview page.
- **Verify a sample afterwards** by magic bytes rather than size — a wrong-but-
  plausible file is the failure mode that matters:

```bash
aws s3 cp s3://somics-dev/raw/<dataset_id>/<file> - | head -c 8 | xxd
```

- **ETags are not comparable** between a multipart upload and a single-part
  copy. Compare byte counts and content, not ETags.

## Cost

~1.6 TB at S3 Standard in us-east-1 is roughly **$37/month**, plus a few hours
of instance time. Inbound transfer to S3 is free. Consider Intelligent-Tiering
on the `raw/` prefix — these are write-once, read-rarely bundles.
