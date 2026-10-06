# LightX2V-derived SLA code

The existing [MIT LICENSE](LICENSE), including Copyright (c) 2026 PlagueKind,
is retained for the wrapper and other MIT-covered portions. It does not replace
the Apache-2.0 terms for the following LightX2V-derived files:

| Bundled path relative to this directory | Upstream path |
|---|---|
| `ComfyUI-H3-SLA-Attention/sla/kernel.py` | `lightx2v/common/ops/attn/kernels/sla_kernel_ar.py` |
| `ComfyUI-H3-SLA-Attention/sla/block_map.py` | `lightx2v/common/ops/attn/utils/sla_util_blhd.py` |

The full license is included in [LICENSE-APACHE-2.0.txt](LICENSE-APACHE-2.0.txt),
copied from the [official Apache-2.0 text](https://www.apache.org/licenses/LICENSE-2.0.txt).
Retain this text, this mapping, the existing MIT license, and all source
attribution and modification notices when copying or redistributing this pack.

Upstream comparison reference:
[ModelTC/LightX2V commit 0c2edc124227fcb6e22399e12c35f23298a7299a](https://github.com/ModelTC/LightX2V/tree/0c2edc124227fcb6e22399e12c35f23298a7299a).
Its root LICENSE contains Apache-2.0; no separate NOTICE file was found in that
revision. This reference documents the comparison, not the original vendoring
commit. This file is a distribution scope note, not an upstream NOTICE.

The existing source headers describe the forward-only port, masked-load fixes,
minimum top-k count, and pooled smooth-k change. Those headers and source files
are unchanged by the 2026-10-06 license-text inclusion fix.
