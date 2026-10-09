#!/usr/bin/env bash
# Fetch the CLIP ViT-B/32 (OpenAI, MIT licence) ONNX files used for scene
# tagging, pinned to one Hugging Face revision and verified by SHA-256.
#   tools/fetch_clip_model.sh          image encoder bundled into the app
#   tools/fetch_clip_model.sh --text   also the text encoder + tokenizer, only
#                                      needed to regenerate labels.json
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="Xenova/clip-vit-base-patch32"
REVISION="d15189d7028b43f1d3e65039190477f6af591c2a"

fetch() {
  local remote="$1" target="$2" sha="$3"
  if [[ -f "$target" ]] && [[ "$(shasum -a 256 "$target" | cut -d' ' -f1)" == "$sha" ]]; then
    return
  fi
  mkdir -p "$(dirname "$target")"
  curl -sSfL -o "$target.part" "https://huggingface.co/$REPO/resolve/$REVISION/$remote"
  local actual
  actual="$(shasum -a 256 "$target.part" | cut -d' ' -f1)"
  if [[ "$actual" != "$sha" ]]; then
    rm -f "$target.part"
    echo "SHA-256 mismatch for $remote: $actual" >&2
    exit 1
  fi
  mv "$target.part" "$target"
  echo "Fetched $target"
}

fetch onnx/vision_model_quantized.onnx "$ROOT/assets/models/clip/vision_model_quantized.onnx" \
  583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299
if [[ "${1:-}" == "--text" ]]; then
  fetch onnx/text_model_quantized.onnx "$ROOT/tools/clip_text/text_model_quantized.onnx" \
    73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a
  fetch tokenizer.json "$ROOT/tools/clip_text/tokenizer.json" \
    f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd
fi
