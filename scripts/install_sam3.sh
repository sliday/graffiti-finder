#!/usr/bin/env bash
# Install Meta's SAM3 into the uv-managed venv.
#
# Prereqs:
#   - HF auth: huggingface-cli login   (one-time)
#   - Access granted on huggingface.co/facebook/sam3
#
# Usage:
#   bash scripts/install_sam3.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Clone the repo into vendor/ (kept out of git via .gitignore).
mkdir -p vendor
if [ ! -d vendor/sam3 ]; then
  git clone --depth 1 https://github.com/facebookresearch/sam3.git vendor/sam3
fi

# 2. Install the package (editable) into the uv venv.
uv pip install -e ./vendor/sam3

# 3. Verify import works.
uv run python -c "from sam3.model_builder import build_sam3_image_model; print('sam3 importable')"

echo "SAM3 install complete. Try: uv run python scripts/sam3_vs_dino.py"
