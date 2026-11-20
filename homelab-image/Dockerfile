# A patch-overlay image: the upstream release, plus this fork's changed files.
#
# WHY NOT build the repository's own Dockerfile: the cluster builder
# (scripts/build-image.sh) delivers its context as a ConfigMap with two hard
# properties - only TOP-LEVEL files are copied, and the whole thing is capped
# at 1 MiB. This repository is ~14 MB across app.py plus routes/, services/,
# templates/ and Database/, so its own Dockerfile cannot be built there
# without changing the builder. An overlay is small, reproducible, and states
# exactly which files differ from upstream.
#
# THE BASE IS PINNED BY DIGEST, not by tag, for the same reason the live
# Deployment is: a repushed tag must not silently change what this fork runs
# on. The digest below is the multi-arch index for upstream 1.55.3, the
# version this patch series was written against and the one deployed.
#
# THE CONTEXT IS GENERATED. `make-context.sh` copies the changed files out of
# the checkout into .context/ under flat names (the ConfigMap would drop the
# subdirectories), with this Dockerfile. Build with:
#
#   homelab/scripts/dev/dev-build.sh spotifystatstracker \
#     --context homelab-image/.context --image spotify-tracker:1.55.3-homelab.1
#
# `homelab-image/README.md` records how the base was verified against tag
# 1.55.3 and how to check the result.
FROM docker.io/i7gamer/spotify-tracker:1.55.3@sha256:79b40166b07376de7d2646272eaa0e7b9578c5512e59c464f65a544545e12b30

# Each COPY overwrites one file that exists in the base; nothing is added to a
# path the app does not already read. Flat source names are a builder
# constraint (see above), not a design choice.
COPY app.py /app/app.py
COPY config.py /app/config.py
COPY auth.py /app/routes/auth.py
COPY trusted_header_route.py /app/routes/trusted_header.py
COPY trusted_header_service.py /app/services/trusted_header.py
COPY connect_spotify.html /app/templates/connect_spotify.html
