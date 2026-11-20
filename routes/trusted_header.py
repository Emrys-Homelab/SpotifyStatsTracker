# SPDX-FileCopyrightText: 2026 i7Gamer
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The trusted-header request path: authenticate, then ask for Spotify cookies.

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
* A first login lands on ``/connect-spotify``. Header-only identity is enough
  to log in but NOT to track: ``is_user_logged_in()`` is False while
  ``users.cookies_json`` is NULL, and the per-user listener that records plays
  is built from those cookies. The cookie page is where that one remaining
  credential is supplied, and the ownership check is the same one the
  standalone forms use - the Spotify account's email must match the trusted
  email, or the listener would (correctly) refuse to record as contamination.

The refusal branches are deliberately NOT redirects to the login form:
an untrusted peer, or a missing identity header, is a misconfiguration or an
attempted bypass, and answering it with a page that has a form invites the
operator to "just log in" past the thing that is wrong.
"""
import logging

from flask import Response, abort, redirect, render_template, request, session, url_for

from config import SESSION_VERSION_KEY, TRUSTED_HEADER_SESSION_KEY
from routes.auth import COOKIE_OWNERSHIP_ERROR
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

        # Identity is settled; the Spotify credential may not be. The test is
        # the app's own notion of a usable session - stored cookies AND a
        # listener that authenticated with them - not merely a non-NULL
        # cookies_json column. An expired or rejected session must land on the
        # cookie page too: get_user_db() re-raises a listener construction
        # failure, and without this check the dashboard answers 500 instead of
        # offering the one action that fixes it. /connect-spotify is the page
        # allowed through without a session; a non-GET is refused rather than
        # redirected, because a form post followed by a 302 is a silent loss.
        if (request.endpoint != "connectSpotify"
                and not dashboard.is_user_logged_in(identity.email)):
            if request.method in ("GET", "HEAD"):
                return redirect(url_for("connectSpotify"))
            return _forbidden("no usable Spotify session is connected to this account")

        return None

    @app.route("/connect-spotify", methods=["GET", "POST"])
    def connectSpotify():
        """Supply the one credential the SSO proxy cannot: a Spotify session."""
        email = session.get("email")
        if not email or session.get(TRUSTED_HEADER_SESSION_KEY) is not True:
            abort(403)
        username = dashboard.get_username_for_email(email)
        if not username:
            abort(403)

        error = None
        if request.method == "POST":
            cookies = request.form.get("cookies", "").strip()
            if not cookies:
                error = "Paste the cookies from your Spotify session."
            else:
                parsed = dashboard.verifyCookiesForEmail(cookies, email)
                if parsed is None:
                    error = COOKIE_OWNERSHIP_ERROR.format(email=email)
                else:
                    dashboard.repo.setUserCookies(username, parsed)
                    # A user who re-connects after an expiry already has a
                    # Database (and a dead listener); a first login does not.
                    # Both paths must leave a listener built from the NEW
                    # cookies and no stale login-status cache.
                    dashboard._invalidateLoginCache(email)
                    if username in dashboard.user_databases:
                        dashboard._refresh_user_session(username, email)
                    else:
                        dashboard.get_user_db(username, email)
                    return redirect(url_for("dashboard"))

        return render_template(
            "connect_spotify.html",
            email=email,
            error=error,
            has_cookies=dashboard.repo.getUserCookies(username) is not None,
        )
