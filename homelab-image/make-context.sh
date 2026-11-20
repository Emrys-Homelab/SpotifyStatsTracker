#!/usr/bin/env bash
# Stage the flat build context for homelab-image/Dockerfile.
#
# WHY FLAT: scripts/build-image.sh turns the context into a ConfigMap with
# `kubectl create configmap --from-file=DIR`, which copies only the files at
# the top level (subdirectories are silently dropped) and caps the whole thing
# at 1 MiB. The repository is ~14 MB and mostly subdirectories, so the context
# is a generated directory of renamed files instead of the checkout.
#
# THE SOURCE IS THE CHECKOUT. Every copied file is read from this repository,
# so the image is the working tree that was tested - not a second copy that can
# drift. The manifest this writes names every file and its sha256; compare it
# with `sha256sum` in the built image to prove the overlay landed.
set -euo pipefail

here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
root="$(CDPATH= cd -- "$here/.." && pwd)"
ctx="$here/.context"

rm -rf "$ctx"
mkdir -p "$ctx"

copy() {  # <source relative to repo root> <flat name in the context>
  local source="$root/$1" name="$2"
  [ -f "$source" ] || { echo "make-context: missing $1" >&2; exit 1; }
  cp "$source" "$ctx/$name"
  printf '%s  %s\n' "$(sha256sum "$source" | cut -d' ' -f1)" "$1" >> "$ctx/patched-files.txt"
}

: > "$ctx/patched-files.txt"
copy app.py app.py
copy config.py config.py
copy routes/auth.py auth.py
copy routes/trusted_header.py trusted_header_route.py
copy services/trusted_header.py trusted_header_service.py
copy templates/connect_spotify.html connect_spotify.html
cp "$here/Dockerfile" "$ctx/Dockerfile"

echo "make-context: staged $ctx"
cat "$ctx/patched-files.txt"
