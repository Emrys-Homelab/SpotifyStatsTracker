# homelab-image: the patch-overlay build

This directory is how the fork is built in-cluster, and it exists because the
builder cannot take this repository's own Dockerfile.

## Why an overlay

`homelab/scripts/build-image.sh` turns its build context into a ConfigMap with
`kubectl create configmap --from-file=DIR`. Two consequences are load-bearing:

* **Only top-level files are copied.** Subdirectories are silently skipped.
  This repository is ~14 MB across `routes/`, `services/`, `templates/`,
  `Database/` and `static/`, so copying the checkout would build an image
  missing almost all of the app.
* **The context is capped at ~900 KB** by the script's own guard (the
  ConfigMap's 1 MiB limit). Even a tar of the source is larger.

The overlay avoids both: the base is the upstream release image, pinned by
digest, and the context is the flat files that replace or add exactly the
files this patch series changed (six for trusted-header mode; the decoupled
identity change adds the schema, user-query, listener, worker and template
files it touches). `make-context.sh` stages them from the
checkout, so there is no second copy to drift.

## Build

```sh
# from the checkout root, in the dev environment
homelab-image/make-context.sh
~/homelab/scripts/dev/dev-build.sh spotifystatstracker \
  --context homelab-image/.context \
  --image registry.emrys.dev/homelab/spotify-tracker:1.55.3-homelab.1
```

`make-context.sh` prints a sha256 for every source file; the same values are
written to `patched-files.txt` in the context.

## Why the base digest is trusted

The `FROM` line is pinned to the multi-arch index for upstream **1.55.3** - the
version the live deployment runs and the version this patch series was written
against. Before the first patched build, the base image was verified against a
pristine `git archive 1.55.3` checkout:

```text
files in the image (excluding __pycache__): 293
files compared with tag 1.55.3:              293
files differing:                               0
files in the image with no tag counterpart:    0
```

Every file the image ships is byte-identical to the tag; the remaining tag
files are excluded by the repository's `.dockerignore` (tests, docs, dev
tooling). So a change in the built image can only come from the overlay.

After a build, the same comparison can be run against the result: all files
except the six below must be identical to the base, and the six must match
`patched-files.txt`:

| Context file | Destination in the image | Change |
| --- | --- | --- |
| `app.py` | `/app/app.py` | trusted-header init, request path, cookie verification |
| `config.py` | `/app/config.py` | the two settings and the session key |
| `auth.py` | `/app/routes/auth.py` | cookie verification delegated to the shared helper |
| `trusted_header_route.py` | `/app/routes/trusted_header.py` | new: the hook and `/connect-spotify` |
| `trusted_header_service.py` | `/app/services/trusted_header.py` | new: settings parsing, peer check, identity |
| `connect_spotify.html` | `/app/templates/connect_spotify.html` | new: the cookie page |

## What the next patch should not assume

This path works because the overlay is small. A change that adds a static
asset, a migration file or a new package would need either a bigger context or
a different builder input (a tar/OCI context or a git fetch in the build Job).
The real fix belongs in `scripts/build-image.sh`, not in a larger overlay.
