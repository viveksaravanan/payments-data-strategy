#!/usr/bin/env bash
# Deploy the current working tree to the HuggingFace Space as a single
# ORPHAN commit (code only). Run via `make deploy`.
#
# Why an orphan: a plain `git push hfspace main` fails because (1) the Space
# has a hard 1 GB storage cap and (2) this branch's history still carries
# ~2 GB of old LFS parquet blobs (v4 deploy commits) that git-lfs would try
# to re-upload. An orphan commit has NO history, so git-lfs never touches
# those blobs — only the current tree (code + the 6 logo PNGs) is pushed.
#
# The Space runs code only; `data/` is gitignored and downloaded on boot
# from the companion HF Dataset repo (see streamlit_app.py). If you
# REGENERATED the data, run `make push-data` first — this script does NOT
# touch the data.
set -euo pipefail

REMOTE="hfspace"
TARGET_BRANCH="main"
TMP_BRANCH="hf-deploy-$$"

cd "$(git rev-parse --show-toplevel)"

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "error: no '$REMOTE' git remote configured." >&2
  exit 1
fi

# Remember where to return to (branch name, or raw SHA if detached).
orig_ref="$(git symbolic-ref --quiet --short HEAD || git rev-parse HEAD)"
short="$(git rev-parse --short HEAD)"

cleanup() {
  git checkout --quiet "$orig_ref" 2>/dev/null || true
  git branch -D "$TMP_BRANCH" 2>/dev/null || true
}
trap cleanup EXIT

echo ">> Deploying current tree ($short) to $REMOTE:$TARGET_BRANCH as an orphan commit…"
git checkout --quiet --orphan "$TMP_BRANCH"
git add -A                       # respects .gitignore -> data/ excluded
git commit --quiet -m "deploy: ${short} — code-only (data served from HF Dataset on boot)"
git push "$REMOTE" "$TMP_BRANCH:$TARGET_BRANCH" --force

echo ">> Pushed. The Space will rebuild (~1-2 min); the first visit then"
echo ">> downloads the dataset (~2 GB, a few minutes) and renders."
