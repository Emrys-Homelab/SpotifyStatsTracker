# SPDX-FileCopyrightText: 2026 i7Gamer
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Trusted-header identity: who a request is, when a proxy already said so.

This app normally proves identity itself - a password plus Spotify cookies that
belong to the same email (see routes/auth.py's _verifiedCookies). A deployment
behind Authelia's forward-auth (or oauth2-proxy) has already proved it, and
asking again is the second login this module exists to remove.

The security problem is not reading the header; it is believing it. Any client
that can reach the process can send ``Remote-User: admin``. So header trust is
both opt-in AND constrained:

  * ``TRUSTED_HEADER_AUTH=1`` turns it on. Unset (the default) leaves every
    upstream path - login form, registration, password reset - exactly as it
    was, which is what keeps a standalone instance working.
  * ``TRUSTED_PROXY_CIDRS`` is REQUIRED when it is on: a comma-separated list of
    address ranges the proxy is allowed to connect from. The header is read
    only when the request's TCP peer is inside that list. If the list is
    missing, empty or unparseable the app refuses to start - a header-trust
    mode that cannot name its proxy is a bypass with extra steps, not a
    feature, so there is deliberately no "empty means any peer" default.

The peer address matters more than it looks. ``TRUST_PROXY_HEADERS`` wraps the
app in Werkzeug's ProxyFix, which rewrites ``REMOTE_ADDR`` from
``X-Forwarded-For``; a CIDR check against the rewritten value would compare an
address the CLIENT chose. ProxyFix saves the pre-rewrite environ under
``werkzeug.proxy_fix.orig``, and peerAddress() prefers that, so the check is
always against the socket peer in both configurations. See
tests/test_trusted_header_auth.py for the with/without-ProxyFix cases.

The header NAMES are fixed rather than configurable. A typo in a configurable
name would make the app trust a header nothing sets, and the symptom would be a
login redirect loop rather than a configuration error.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass, field

from config import (
    FALSY_ENV_VALUES,
    TRUSTED_HEADER_AUTH_ENV_VAR,
    TRUSTED_HEADER_EMAIL,
    TRUSTED_HEADER_USER,
    TRUSTED_PROXY_CIDRS_ENV_VAR,
    TRUTHY_ENV_VALUES,
)

logger = logging.getLogger(__name__)

# Where Werkzeug's ProxyFix stashes the values it is about to overwrite. Only
# present when TRUST_PROXY_HEADERS enabled ProxyFix - peerAddress() falls back
# to the plain environ key when it is absent.
PROXY_FIX_ORIGINAL_ENVIRON_KEY = "werkzeug.proxy_fix.orig"

# The domain used to give a Remote-User with no Remote-Email a stable account
# key. users.email is the app's unique identity column; the .invalid TLD is
# reserved by RFC 2606, so this can never collide with a real address.
SYNTHETIC_EMAIL_DOMAIN = "trusted-header.invalid"

# Deliberately conservative. Remote-User becomes part of a synthetic email and
# a display/account name; a value with whitespace, control characters or an "@"
# is refused rather than sanitised into something that might collide with a
# different identity.
_USER_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]{1,64}$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]{1,254}@[^@\s]{1,253}$")


class TrustedHeaderConfigError(RuntimeError):
    """TRUSTED_HEADER_AUTH is on but cannot be applied safely.

    Raised during app construction, so the process refuses to boot instead of
    starting a header-trust mode with no proxy allowlist. A boot failure is
    loud, attributable and cannot be mistaken for a working login.
    """


@dataclass(frozen=True)
class TrustedHeaderConfig:
    """The validated header-trust settings for one process."""

    enabled: bool = False
    networks: tuple = field(default_factory=tuple)
    user_header: str = TRUSTED_HEADER_USER
    email_header: str = TRUSTED_HEADER_EMAIL


@dataclass(frozen=True)
class TrustedIdentity:
    """The identity a trusted proxy asserted for one request."""

    user: str
    email: str


def loadTrustedHeaderConfig(environ=None) -> TrustedHeaderConfig:
    """Read and validate the two settings, or raise TrustedHeaderConfigError.

    ``environ`` exists for tests; production always passes None (os.environ).
    Read once at construction, like the proxy-hop count: a value that changes
    the trust decision must not be able to change under a live process.
    """
    env = os.environ if environ is None else environ

    raw = env.get(TRUSTED_HEADER_AUTH_ENV_VAR, "").strip().lower()
    if not raw or raw in FALSY_ENV_VALUES:
        return TrustedHeaderConfig(enabled=False)
    if raw not in TRUTHY_ENV_VALUES:
        # A typo must not silently disable the feature the operator asked for:
        # the deployment would fall back to its own login form and look like it
        # worked. Failing here names the variable instead.
        raise TrustedHeaderConfigError(
            f"{TRUSTED_HEADER_AUTH_ENV_VAR}={raw!r} is not a recognised on/off value "
            f"(use one of {sorted(TRUTHY_ENV_VALUES)}, or unset it to disable header auth)")

    cidrRaw = env.get(TRUSTED_PROXY_CIDRS_ENV_VAR, "").strip()
    if not cidrRaw:
        raise TrustedHeaderConfigError(
            f"{TRUSTED_HEADER_AUTH_ENV_VAR} is set but {TRUSTED_PROXY_CIDRS_ENV_VAR} is empty. "
            "Header auth is only safe when the proxy's source range is named; set "
            f"{TRUSTED_PROXY_CIDRS_ENV_VAR} to the proxy's pod/service CIDR (for example "
            "10.0.0.0/8), or unset the feature.")

    networks = []
    for item in cidrRaw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError as exc:
            raise TrustedHeaderConfigError(
                f"{TRUSTED_PROXY_CIDRS_ENV_VAR} contains {item!r}, which is not an IP network "
                f"in CIDR form: {exc}") from exc
    if not networks:
        raise TrustedHeaderConfigError(
            f"{TRUSTED_PROXY_CIDRS_ENV_VAR} contained no usable networks: {cidrRaw!r}")

    return TrustedHeaderConfig(enabled=True, networks=tuple(networks))


def peerAddress(environ) -> str:
    """The TCP peer's address, before any X-Forwarded-For rewriting.

    ProxyFix replaces REMOTE_ADDR with the client-supplied X-Forwarded-For
    value. When it has run, the original address is kept in
    ``werkzeug.proxy_fix.orig`` and that is what a source-range check must use.
    When TRUST_PROXY_HEADERS is unset ProxyFix never ran, the key is absent,
    and the plain REMOTE_ADDR is already the socket peer.
    """
    original = environ.get(PROXY_FIX_ORIGINAL_ENVIRON_KEY) or {}
    if isinstance(original, dict):
        saved = original.get("REMOTE_ADDR")
        if saved:
            return str(saved).strip()
    return str(environ.get("REMOTE_ADDR") or "").strip()


def isTrustedPeer(address: str, networks) -> bool:
    """Whether ``address`` is inside one of ``networks``.

    IPv4-mapped IPv6 (``::ffff:10.0.0.1``) is folded to IPv4 first: a
    dual-stack listener reports v4 peers that way, and the operator's CIDR is
    written as v4. An unparseable or empty address is never trusted.
    """
    if not address:
        return False
    try:
        # A scoped IPv6 address ("fe80::1%eth0") is not accepted by
        # ip_address(); the scope is interface-local detail, not part of the
        # range comparison.
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    return any(parsed in network for network in networks)


def identityFromHeaders(headers, config: TrustedHeaderConfig) -> TrustedIdentity | None:
    """The identity the proxy asserted, or None when the headers are unusable.

    Remote-Email is optional: Authelia sets it, but a proxy that only forwards
    a username must not create an account it can never find again. A synthetic
    email under a reserved domain gives that username a stable key in the
    existing users.email column. When both are present, the email is the
    account key - the same key the standalone login uses - so an existing
    account with that address keeps its history and its stored cookies.
    """
    user = (headers.get(config.user_header) or "").strip()
    if not user or not _USER_PATTERN.match(user):
        return None

    email = (headers.get(config.email_header) or "").strip().lower()
    if email:
        if not _EMAIL_PATTERN.match(email):
            return None
    else:
        email = f"{user.lower()}@{SYNTHETIC_EMAIL_DOMAIN}"
    return TrustedIdentity(user=user, email=email)
