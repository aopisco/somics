# Environment plate credits

## `alps_field_3k.jpg`

| | |
|---|---|
| **Asset** | Alps Field (`alps_field`) |
| **Author** | Andreas Mischok |
| **Source** | https://polyhaven.com/a/alps_field |
| **Licence** | **CC0 1.0 Universal — public domain dedication** |
| **Where shot** | 46.609194 N, 9.429675 E — the Hinterrhein valley, Graubünden, Switzerland |
| **Shot** | 14 April 2022 |
| **In this repo** | 3072 x 1536 equirectangular JPEG, 1.25 MB |

Verified on the asset page and on https://polyhaven.com/license before committing: Poly
Haven publishes everything under CC0, "You do not need to give credit or attribution when
using them (although it is appreciated)." This file exists because it is appreciated, and
because the next person to touch the plate should not have to re-derive its provenance.

### How the committed file was made

Poly Haven's source is a 20K unclipped HDRI; the `.hdr` downloads run from 6.5 MB (2K) to
615 MB (20K), which is not something to put in a web bundle. The committed image is
downsampled from Poly Haven's own tone-mapped JPEG of the same asset
(`HDRIs/extra/Tonemapped JPG/alps_field.jpg`, 8192 x 4096, 53 MB), Lanczos-resampled and
re-encoded as progressive JPEG at quality 84. Being already tone-mapped is why `Sky.tsx`
draws it with `toneMapped: false` — see the comment there.

2K (566 KB) was tried first and was the brief's preference, but at the viewer's default
framing it is visibly mushy: a 1190 px canvas over roughly 50 degrees of a 360 degree plate
wants about 8600 px of panorama for a 1:1 read, so every step up is real. 3K buys legible
trees, terraces and rooflines on the far slope for 700 KB, and still leaves headroom under
the 2 MB ceiling. 4K was 1.83 MB, which was too close to the ceiling for the extra.
