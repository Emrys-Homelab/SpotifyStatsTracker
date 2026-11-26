# SpotifyStatsTracker — homelab fork

This is the emrys.dev homelab's fork of
[SpotifyStatsTracker](https://github.com/i7Gamer/SpotifyStatsTracker), a self-hosted Spotify
listening-history tracker. It replaces the app's own login with the identity the cluster's
forward-auth proxy sends, and separates that login identity from the Spotify account whose
listening is tracked.

**Upstream project: https://github.com/i7Gamer/SpotifyStatsTracker**

Upstream documents installation, the Spotify application setup and the dashboard; this README
covers only the fork. The unedited upstream README is preserved at
[README.upstream.md](https://github.com/Emrys-Homelab/SpotifyStatsTracker/blob/main/README.upstream.md).

## What this fork changes

- **0001 — trusted-header login.** `TRUSTED_HEADER_AUTH=1` accepts `Remote-User`/`Remote-Email`
  from an allowlisted proxy. `TRUSTED_PROXY_CIDRS` is required and checked against the real TCP
  peer, so a direct client cannot forge the header; the app's own login, registration and reset
  routes answer 404 in this mode.
- **0002 — first login lands on Spotify.** A request with no stored Spotify session goes to
  `/connect-spotify`, and a dead stored session lands there instead of a 500.
- **0003 — trusted-header documentation.** `docs/trusted-header-auth.md`: settings, the peer
  check, the cookie step, and the NetworkPolicy/SecurityPolicy the mode depends on.
- **0004 — overlay build.** `homelab-image/` pins the upstream image by digest and replaces only
  the changed files, so the in-cluster builder does not need the 14 MB upstream tree.
- **0005 — Spotify account columns.** Nullable `users.spotify_account_email` and
  `spotify_account_name`, added to an existing SQLite file idempotently at startup.
- **0006 — connect accepts the real account.** `/connect-spotify` records the cookies whatever
  their account email, reads the Spotify profile, stores it and reports what it found.
- **0007 — the listener records what it observes.** A Spotify account that differs from the login
  email no longer stops recording; a mid-session change still warns and rebaselines.
- **0008 — the UI leads with the Spotify account.** The nav, account page and dashboard greet the
  tracked Spotify account, with the SSO email kept as "Signed in as".
- **0009 — overlay stages the identity files.** The additional schema, query, listener, worker,
  template and CSS files are added to the build context.
- **0010 — documentation updated** for the decoupled login and Spotify identity.

## Branches

| Branch | What it is |
| --- | --- |
| `main` | upstream tag `1.55.3` plus one documentation commit: this README, `README.upstream.md`, `FORK.md` and `patches/`. No code changes. |
| `homelab` | the fork: upstream `1.55.3` plus the ten patches, and the same README. This is the branch to build. |

Base: upstream `1.55.3`, commit `0b72feda1046f42494d666d3f5f905d78cd94f72` (the repository has no
upstream tags mirrored; the commit is the release commit).

## Build and deploy

The image is an overlay on the upstream image, built by the in-cluster builder. The upstream tree
is ~14 MB across subdirectories and `build-image.sh` delivers its context as flat files, so
`homelab-image/` pins the upstream image by digest and replaces only the changed files.
`homelab-image/make-context.sh` stages the 17 changed files; `~/homelab/scripts/build-image.sh`
builds and pushes them:

```sh
homelab-image/make-context.sh
~/homelab/scripts/build-image.sh homelab-image/.context spotify-tracker:1.55.3-homelab.5
```

The deployment pins the digest:

```
registry.emrys.dev/homelab/spotify-tracker:1.55.3-homelab.5@sha256:8a75ec799e28a322dc83b2187cb3ed9ce1e6b538f7266a743d77098e7fff867c
```

It lives in `~/backup/gitops/apps/spotify-tracker/deployment.yaml`. `.4` was the first build of
the series; `.5` is the same code with one label capitalised. The staged context is ~740 KB, above
the 256 KB a client-side `kubectl apply` accepts, so the `.4`/`.5` builds used `build-image.sh`
with `kubectl apply --server-side`; keep the context under the limit or apply server-side.

## Patches

The series is in [`patches/`](https://github.com/Emrys-Homelab/SpotifyStatsTracker/tree/main/patches),
one file per commit. Patch 0001 also edits upstream's configuration table, so apply the series to
the upstream base, then restore this repository's README files:

```sh
git clone https://github.com/Emrys-Homelab/SpotifyStatsTracker.git
cd SpotifyStatsTracker
git checkout -B rebuilt 0b72fed                        # upstream 1.55.3
git restore --source=main --worktree patches           # the series lives on main
git am patches/*.patch                                 # 0001 also edits upstream's table
git checkout main -- README.md README.upstream.md
rm -rf patches
```

The tree is then identical to `homelab`.

To rebase onto a newer upstream release:

```sh
git fetch https://github.com/i7Gamer/SpotifyStatsTracker 'refs/tags/*:refs/tags/*'
git checkout -B rebase 1.56.0
git am -3 patches/*.patch          # resolve conflicts here
git format-patch 1.56.0..rebase -o patches/
```

Then point `homelab` at the rebased branch, rebuild `main` as the new tag plus a documentation
commit carrying `README.md`, `README.upstream.md`, `FORK.md` and the regenerated `patches/`,
rebuild the image, repin the digest, and deploy.

## Caveats

- **Trusted-header mode trusts the whole peer CIDR, and the live deployment allows the whole pod
  network.** The app refuses to start without `TRUSTED_PROXY_CIDRS`, and believes
  `Remote-User`/`Remote-Email` only when the TCP peer is inside one of those CIDRs. The header is
  set by the Gateway's `ext_authz` after Authelia authorises the request, but the live list is
  `10.42.0.0/16` and, as of this README, no NetworkPolicy restricts ingress to the app. Any pod
  that can reach the Service can therefore claim any identity; add that ingress policy before
  relying on the header as authentication.
- The app's own login, registration and password-reset routes answer 404 in this mode; accounts
  are created on first sight from the proxy's header.
- The login email and the Spotify account are deliberately independent. The dashboard shows the
  Spotify account, with the SSO email as "Signed in as"; a mid-session Spotify account change
  warns and rebaselines rather than blocking recording.
- The image is an overlay pinned to the upstream digest: a new upstream release means
  regenerating the context, rebuilding and repinning.
- **AGPL-3.0-or-later**: running a modified version as a network service obliges you to offer its
  source. This repository is that source.

## Licence

Unchanged from upstream: **AGPL-3.0-or-later**
([COPYING](https://github.com/Emrys-Homelab/SpotifyStatsTracker/blob/main/COPYING)).
