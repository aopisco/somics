# Visium ingest verification

atlas: `s3://somics-dev/rebuild/atlas/2026-09-02T00-43-52Z`

**16/16 checks passed**

| section | check | result | detail |
|---|---|---|---|
| LIBD_151507 | section present | ok | 1 row(s) |
| LIBD_151507 | tissue | ok | 'dorsolateral prefrontal cortex' |
| LIBD_151507 | disease_state | ok | 'healthy' |
| LIBD_151507 | obs rows | ok | 4226 |
| LIBD_151507 | obs.technology | ok | ['visium'] |
| LIBD_151507 | obs.spatial_unit | ok | ['spot'] |
| LIBD_151507 | obs.organism | ok | ['Homo sapiens'] |
| LIBD_151507 | obs.unit_size_um | ok | [55.0] |
| LIBD_151507 | n_counts > 0 | ok | min 63.0 |
| LIBD_151507 | expression row sums == n_counts | ok | 64 rows sampled, 33538 features |
| LIBD_151507 | image row | ok | 1 |
| LIBD_151507 | image_modality | ok | 'he' |
| LIBD_151507 | obs inside image | ok | max x 10917/13332, max y 11751/13332 |
| LIBD_151507 | pixel_size image == obs | ok | 0.57069 |
| LIBD_151507 | he_crop shape | ok | (16, 128, 128, 3) |
| LIBD_151507 | crops land on tissue | ok | mean intensity at top spots 135.5 vs random 158.1 |
