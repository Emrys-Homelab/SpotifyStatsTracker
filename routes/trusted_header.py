# SPDX-FileCopyrightText: 2026 i7Gamer
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The trusted-header request path: authenticate from the proxy's assertion.

Registered only when ``TRUSTED_HEADER_AUTH`` is on (see
services/trusted_header.py for the settings and the peer check). What this
module adds on top of it:

* A ``before_request`` hook that turns a valid proxy assertion into the same
  signed session the app's own login produces, so every existing
  ``get_current_user_or_redirect()`` call site works unchanged. This is the
  single choke point: the routes were written against that helper, not against
  a decorator that could be forgotten.
* The app's own doors close. ``/login``, ``/register`` and ``/reset-password``
  answer 404 rather than a form nobody should use; ``/logout`` becomes a
  redirect (Authelia owns the real session).

The refusal branches are deliberately NOT redirects to the login form:
an untrusted peer, or a missing identity header, is a misconfiguration or an
attempted bypass, and answering it with a page that has a form invites the
operator to "just log in" past the thing that is wrong.
"""
import logging

from flask import Response, abort, redirect, request, session, url_for

from config import SESSION_VERSION_KEY, TRUSTED_HEADER_SESSION_KEY
from services.trusted_header import (
    identityFromHeaders,
    isTrustedPeer,
    peerAddress,
)

logger = logging.getLogger(__name__)

# Routes that must keep working without an identity header. /health is the
# kubelet's probe (its peer is the node, never the proxy) and static assets
# carry no account data; refusing either would break the pod or make a refusal
# page unreadable.
OPEN_ENDPOINTS = frozenset({"static"})
OPEN_PATHS = frozenset({"/health"})

# The standalone login surface. In header mode these are not merely hidden:
# answering them at all would leave a second way in that bypasses the proxy's
# identity, and a form whose password path is guaranteed to fail.
CLOSED_ENDPOINTS = frozenset({"login", "register", "resetPassword"})


def _forbidden(message: str) -> Response:
    return Response(f"Forbidden: {message}\n", status=403, mimetype="text/plain")


def _establishSession(dashboard, email: str, username: str) -> None:
    """Make the signed session match the proxy's identity.

    A no-op when it already does, so an ordinary page request does not rewrite
    the session cookie on every response. A user SWITCH clears first - the same
    rule /login follows, so one account's one-shot OAuth state or queued flash
    cannot cross into the next.
    """
    version = dashboard.repo.getUserSessionVersion(username) or 0
    if (session.get("email") == email
            and session.get("username") == username
            and session.get(SESSION_VERSION_KEY, 0) == version
            and session.get(TRUSTED_HEADER_SESSION_KEY) is True):
        return

    if session.get("email") != email:
        session.clear()
    session.permanent = True
    session[TRUSTED_HEADER_SESSION_KEY] = True
    session["email"] = email
    session["username"] = username
    session[SESSION_VERSION_KEY] = version


def register(app, dashboard) -> None:
    config = dashboard.trustedHeader
    if not config.enabled:
        return

    @app.before_request
    def _trustedHeaderLogin():
        if request.endpoint in OPEN_ENDPOINTS or request.path in OPEN_PATHS:
            return None

        peer = peerAddress(request.environ)
        if not isTrustedPeer(peer, config.networks):
            # The forged-header case. Logged at WARNING because it is either an
            # attempt to bypass the proxy or a Service that is reachable
            # without it - both are the operator's problem, not noise.
            logger.warning(
                "Refusing %s %s: peer %s is outside %s",
                request.method, request.path, peer or "<unknown>",
                ", ".join(str(network) for network in config.networks))
            return _forbidden("this app is only reachable through its authenticating proxy")

        if request.endpoint in CLOSED_ENDPOINTS:
            abort(404)

        if request.endpoint == "logout":
            # There is no local password session to end any more. Clearing the
            # cookie then returning to / lets the proxy re-assert the identity
            # immediately, which is honest: signing out of the tracker is not
            # signing out of Authelia (that would end every app's session).
            session.clear()
            return redirect(url_for("dashboard"))

        identity = identityFromHeaders(request.headers, config)
        if identity is None:
            logger.warning(
                "Refusing %s %s: trusted peer %s sent no usable %s",
                request.method, request.path, peer, config.user_header)
            return _forbidden("the authenticating proxy did not supply an identity")

        username = dashboard.get_username_for_email(identity.email)
        if not username:
            # First sight. get_or_create_user is the same allocator the
            # standalone register form uses (it also runs the admin bootstrap),
            # so an existing account with this email is reused with its
            # history and stored cookies rather than duplicated.
            username = dashboard.get_or_create_user(identity.email)
            logger.info("Created account %s for trusted header identity %s",
                        username, identity.user)

        _establishSession(dashboard, identity.email, username)
        return None
