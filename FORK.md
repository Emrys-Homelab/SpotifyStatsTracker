# Homelab fork of SpotifyStatsTracker

This is the homelab cluster's fork of [i7Gamer/SpotifyStatsTracker](https://github.com/i7Gamer/SpotifyStatsTracker),
published in full so that a clone reproduces the image running at `spotify.emrys.dev`.

| | |
| --- | --- |
| Upstream | [`i7Gamer/SpotifyStatsTracker`](https://github.com/i7Gamer/SpotifyStatsTracker) (AGPL-3.0-or-later) |
| Base | tag `1.55.3`, commit `0b72feda1046f42494d666d3f5f905d78cd94f72` |
| Fork branch | `homelab` — 10 commits |
| Patches | [`patches/`](patches/) — one file per commit, identical patch-ids |
| Series of record | [`MyrddinE/homelab`](https://github.com/MyrddinE/homelab), `patches/spotify-tracker/` |
| Image | `registry.emrys.dev/homelab/spotify-tracker:1.55.3-homelab.5@sha256:8a75ec799e28a322dc83b2187cb3ed9ce1e6b538f7266a743d77098e7fff867c` |
| Pinned in | `MyrddinE/backup`, `gitops/apps/spotify-tracker/deployment.yaml` |

## Why

Upstream has its own account system and session cookies, so it asks for a second login behind
Authelia. The fork replaces that login with the identity Authelia's forward-auth sends
(`Remote-User` from an allowlisted proxy) and separates the two identities the login conflated:

- **login identity** — who may use the app, owned by the trusted proxy;
- **Spotify account** — whose listening is tracked, read from the Spotify cookies and shown in the UI.

They are deliberately not required to match: in a one-user deployment the SSO email and the Spotify
account differ, and the upstream ownership check refused to record anything because of it.

| Patch | Change |
| --- | --- |
| `0001` | `TRUSTED_HEADER_AUTH` plus a **required** `TRUSTED_PROXY_CIDRS`, checked against the real TCP peer |
| `0002` | `/connect-spotify` and the first-login redirect on a Spotify cookie page |
| `0003` | `docs/trusted-header-auth.md` — settings, peer check, the NetworkPolicy/SecurityPolicy precondition |
| `0004` | `homelab-image/` — a pinned-base overlay build and its context generator |
| `0005` | `users.spotify_account_email` / `spotify_account_name`, idempotently added to an existing SQLite file |
| `0006` | `/connect-spotify` accepts the cookies whatever the account email, records the Spotify profile |
| `0007` | the listener records the observed account instead of refusing on a mismatch; a mid-session change still warns |
| `0008` | the UI leads with the Spotify account; the SSO email stays visible as "Signed in as" |
| `0009` | the overlay stages the additional identity files |
| `0010` | `docs/trusted-header-auth.md` updated for the decoupled flow |

## Branches

| Branch | What it is |
| --- | --- |
| `main` | upstream `1.55.3` plus **one documentation commit** (this file, `patches/`, and a one-line banner in `README.md`). No code changes. |
| `homelab` | the fork: the ten commits the patch series carries. `git log main..homelab` is exactly `patches/*.patch`. |

`main` is the **fork point**, not a live mirror of upstream `main`. Fast-forward it from upstream
when the fork is rebased; `homelab` is then rebased onto it.

## Reproduce

```sh
git clone https://github.com/Emrys-Homelab/SpotifyStatsTracker.git
cd SpotifyStatsTracker && git checkout main
git am patches/*.patch        # the tree is then identical to branch `homelab`
```

## Build and where it is pinned

Built by the in-cluster builder from the `homelab-image/` overlay — the upstream tree is ~14 MB
across subdirectories and `scripts/build-image.sh` delivers its context as a ConfigMap of flat
files, so the overlay pins the upstream image by digest and replaces only the changed files:

```sh
~/homelab/scripts/build-image.sh SpotifyStatsTracker/homelab-image spotify-tracker:1.55.3-homelab.5
```

The deployed reference is the digest:

```
registry.emrys.dev/homelab/spotify-tracker:1.55.3-homelab.5@sha256:8a75ec799e28a322dc83b2187cb3ed9ce1e6b538f7266a743d77098e7fff867c
```

`MyrddinE/backup` pins it in `gitops/apps/spotify-tracker/deployment.yaml`. `.4` was the first build
of the series; `.5` is the same code with the "Continue to Dashboard" label capitalised. The
patch-by-patch detail and the trusted-header security precondition are in `MyrddinE/homelab`,
`patches/spotify-tracker/README.md`.

## Convention

Forks in this cluster are published under the `Emrys-Homelab` account, on branch `homelab`, with the
patch series in `patches/` and the deployed artefact pinned in `MyrddinE/backup`. The written rule is
`MyrddinE/homelab`, `docs/forks.md`. **A fork is not finished until it is pushed.**
