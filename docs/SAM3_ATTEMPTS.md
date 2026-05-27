# SAM3 integration attempts — outcome

**Date:** 2026-05-27
**Decision:** Ship GroundingDINO+CLIP; defer SAM3 to Linux/CUDA deployment
or a Python 3.13 + MLX environment.

## What we tried

### 1. `facebook/sam3` via transformers `Sam3Model`
- HF auth: granted (user has access)
- Class `Sam3Processor.from_pretrained("facebook/sam3")` fails:
  `OSError: Can't load image processor for 'facebook/sam3' ... missing
  preprocessor_config.json`.
- The gated repo ships Meta's own pickled processor, not a transformers-
  compatible one. No quick fix.

### 2. Meta's `sam3` package from GitHub
- `git clone https://github.com/facebookresearch/sam3 vendor/sam3`
- `uv pip install -e ./vendor/sam3` installs the package; needs `einops`,
  `pycocotools`, `opencv-python-headless` as extra deps.
- **Triton blocker:** `sam3/model/edt.py` imports `triton`, which has no
  macOS wheels (Linux+CUDA only).
  - Patched: lazy import in `edt.py`, stub `@triton.jit` when missing.
- **CUDA hardcoding blocker:** 9 inference-path files contain
  `device="cuda"` or `.cuda()` calls. Patched all with sed:
  - `model/decoder.py`, `model/vl_combiner.py`,
    `model/sam3_image_processor.py`, `model/sam3_tracker_base.py`,
    `model/sam3_tracking_predictor.py`, `model/io_utils.py`,
    `model/sam3_video_predictor.py`, `model/video_tracking_multiplex.py`,
    `model/video_tracking_multiplex_demo.py`.
- **Model loads on CPU after patches.** 840.5M parameters via
  `build_sam3_image_model()`.
- **Final blocker — dtype mismatch:**
  `RuntimeError: mat1 and mat2 must have the same dtype, but got
  BFloat16 and Float`
  at `vitdet.py:74` (the FFN layer). The model graph has explicit
  bfloat16 casts that the `.float()` recursion can't unwind. Resolving
  this means either:
    - patching every `.to(torch.bfloat16)` call in the model graph, or
    - running on CUDA where autocast hides the mismatch.

### 3. `mlx-community/sam3-image`
- 3.5 GB quantized MLX checkpoint, Apple Silicon native.
- Repo: `github.com/Deekshith-Dade/mlx_sam3`.
- **Blocker:** requires Python 3.13+. Our project is pinned to 3.12 for
  torch + transformers + playwright wheel coverage.
- Would need a parallel uv environment at 3.13 just for SAM3 inference,
  with results piped to the main pipeline. Doable, not done today.

## What we have instead

GroundingDINO + 5-stage filter chain documented in `vision.py`. Verified
output on Karmelicka: 5 detections, 0 false positives. CLIP confidence
0.72–0.98 per surviving crop.

## What unblocks SAM3 later

| Path | Effort | Reward |
|---|---|---|
| Deploy on Linux/CUDA box (Colab, GCP) | low | Full Meta sam3 works as-is |
| Add Python 3.13 venv + mlx-community model | medium | Native Apple Silicon, 3.5 GB |
| Patch every bf16 cast in vendored sam3 for CPU | high | Slow CPU inference on this Mac |

Vendored patches live in `vendor/sam3/` (gitignored top-level, patches
preserved in the vendored copy). To restore upstream:
`rm -rf vendor/sam3 && bash scripts/install_sam3.sh`.
