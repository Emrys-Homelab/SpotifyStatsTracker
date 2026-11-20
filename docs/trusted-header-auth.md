# Trusted-header SSO

**What this is for:** running the tracker behind a forward-auth proxy (Authelia
through Envoy Gateway, or `oauth2-proxy`) so the proxy's login is the *only*
login. The app reads `Remote-User` / `Remote-Email` from the proxied request,
creates the account on first sight, and never serves its own login form.

**Read it when:** deploying this fork behind a proxy, or debugging a
`403 Forbidden` / a redirect to `/connect-spotify`.

**Upstream behaviour is unchanged when the feature is unset.** With
`TRUSTED_HEADER_AUTH` unset the app is exactly the standalone tracker: own
accounts, own login form, registration and password reset. The feature is an
opt-in mode, not a replacement.

## The two settings

| Variable | Meaning |
| --- | --- |
| `TRUSTED_HEADER_AUTH=1` | Turn header auth on. Unset or `0`/`false`/`no`/`off` leaves normal login. Any other value is a startup error. |
| `TRUSTED_PROXY_CIDRS` | **Required** when the flag is on. Comma-separated IP networks (CIDR), e.g. `10.244.0.0/16,2001:db8::/32`. The identity header is read only when the request's TCP peer is inside one of these ranges. Missing, empty or unparseable values make the app refuse to start. |

The app reads `Remote-User` and `Remote-Email` (fixed names — Authelia's
forward-auth and Envoy's `headersToBackend` use exactly these). `Remote-Email`
is optional: without it the account key is `<user>@trusted-header.invalid`, a
reserved-domain synthetic address, so the same username always resolves to the
same account and can never collide with a real email.

## The security precondition, in code

A process that trusts `Remote-User` without constraining where it comes from is
an authentication bypass: anyone who can reach the port sends
`Remote-User: admin`. This fork therefore encodes the precondition rather than
documenting it as an assumption:

1. **The peer address is checked against `TRUSTED_PROXY_CIDRS` on every
   request.** The check uses the *real TCP peer*, not `request.remote_addr`:
   with `TRUST_PROXY_HEADERS` set, Werkzeug's `ProxyFix` rewrites
   `remote_addr` from the client-supplied `X-Forwarded-For`, so the original
   address is read from ProxyFix's saved environ instead. A forged
   `X-Forwarded-For` therefore cannot move a request into the trusted range.
2. **No allowlist, no startup.** `TRUSTED_HEADER_AUTH=1` with an empty or
   malformed `TRUSTED_PROXY_CIDRS` raises during app construction. There is
   deliberately no "empty means any peer" default.
3. **A refused request is refused, not redirected to a form.** An untrusted
   peer, or a trusted peer without an identity header, gets `403`. `/login`,
   `/register` and `/reset-password` answer `404` in header mode. `/health`
   stays open for the Kubernetes probe, and static assets stay open because
   they carry no account data.

The allowlist is one half. The other half is that the app must be reachable
only through the proxy: keep the Service `ClusterIP`, and add a
`NetworkPolicy` that admits only the proxy's namespace. A CIDR that includes
every pod in the cluster is only safe when the network layer narrows it
further — e.g. Envoy Gateway's data plane in `envoy-gateway-system`, with a
policy admitting that namespace and nothing else. The app-level check is the
second lock, not a replacement for the first.

## What happens on the first login

Header-only identity is enough to **log in, not to track**. The per-user
Spotify listener is built from the cookies stored in `users.cookies_json`, and
`is_user_logged_in()` is false while that column is NULL. So:

1. The proxy authenticates the user and forwards `Remote-User`/`Remote-Email`.
2. The app finds or creates the account keyed by that email. An existing
   standalone account with the same email keeps its history and its stored
   cookies — the account row is reused, not duplicated.
3. If no Spotify session is stored, every page redirects to
   **`/connect-spotify`**. That page carries the same "locate your session
   data" instructions as the login page and saves the pasted cookies.
4. Cookies are verified exactly as the standalone login verifies them: they
   must authenticate to Spotify **and** the Spotify account's email must match
   the trusted email. A session for a different Spotify account is refused —
   the listener would otherwise detect contamination and record nothing.
5. From then on the SSO login is the only prompt; the Spotify cookies are a
   stored data-source credential, not a login.

If the Spotify session later expires, tracking pauses (the app's normal
behaviour) and `/connect-spotify` is where it is replaced. The page is
reachable at any time in header mode, including when a valid session is
already stored.

## Signing out

There is no local password session to end. `POST /logout` clears the tracker's
cookie and returns to `/`, where the proxy immediately re-asserts the same
identity. Ending the SSO session is Authelia's logout (or the proxy's), not
this app's.

## Kubernetes example

```yaml
env:
  - name: TRUSTED_HEADER_AUTH
    value: "1"
  # The Envoy data plane's pod range, narrowed further by the NetworkPolicy.
  - name: TRUSTED_PROXY_CIDRS
    value: "10.244.0.0/16"
  - name: TRUST_PROXY_HEADERS
    value: "1"          # only if X-Forwarded-* are also used for client IPs
```

and the matching ingress policy (namespace names differ per cluster):

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: spotify-tracker-proxy-only
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: spotify-tracker
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: envoy-gateway-system
      ports:
        - protocol: TCP
          port: 5000
```

The app's `SecurityPolicy` must forward the headers to the backend
(`headersToBackend: [Remote-User, Remote-Email, ...]`); without that the proxy
authenticates the user and then sends no identity, which this app answers with
`403`.

## Failure modes

| Symptom | Cause |
| --- | --- |
| Pod does not start; log names `TRUSTED_PROXY_CIDRS` | The flag is on without a proxy allowlist. Set the CIDR range, or unset `TRUSTED_HEADER_AUTH`. |
| Pod does not start; `TRUSTED_HEADER_AUTH='ture' is not a recognised on/off value` | A typo is a startup error on purpose — silently disabling the feature would fall back to the login form and look like it worked. |
| `403 This app is only reachable through its authenticating proxy` | The TCP peer is outside `TRUSTED_PROXY_CIDRS`. Check the proxy's actual source address (not a pod name) and the NetworkPolicy. |
| `403 the authenticating proxy did not supply an identity` | The peer is trusted but `Remote-User` is absent or malformed. Check the `SecurityPolicy`'s `headersToBackend`. |
| Redirect loop to `/connect-spotify` | No Spotify session is stored, or `verifyCookiesForEmail` keeps refusing it. The page's error says which. |
| Login form at `/login` returns 404 | Expected: header mode closes the standalone auth surface. |
