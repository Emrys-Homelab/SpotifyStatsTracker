# SPDX-FileCopyrightText: 2026 i7Gamer
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Trusted-header mode: the settings, the peer check, and the request path.

The security property is the one worth pinning: the identity header is
believed only when the TCP peer is inside TRUSTED_PROXY_CIDRS, and that stays
true when ProxyFix is rewriting REMOTE_ADDR from X-Forwarded-For. The rest of
the file covers the behaviour the operator asked for - first login creates the
account and lands on the cookie page, no header is refused, and the app's own
login surface is closed.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import session as flaskSession

from config import SESSION_VERSION_KEY, TRUSTED_HEADER_SESSION_KEY
from services.trusted_header import (
    SYNTHETIC_EMAIL_DOMAIN,
    TrustedHeaderConfigError,
    identityFromHeaders,
    isTrustedPeer,
    loadTrustedHeaderConfig,
    peerAddress,
)
from tests._app_factory import makeApp

TRUSTED_ENV = {
    "TRUSTED_HEADER_AUTH": "1",
    "TRUSTED_PROXY_CIDRS": "10.0.0.5/32,192.168.1.0/24",
}
PROXY_PEER = "10.0.0.5"
OTHER_PEER = "10.9.9.9"


class LoadConfigTestCase(unittest.TestCase):
    def test_unset_means_disabled(self):
        config = loadTrustedHeaderConfig({})
        self.assertFalse(config.enabled)

    def test_explicit_off_spellings_mean_disabled(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                self.assertFalse(loadTrustedHeaderConfig(
                    {"TRUSTED_HEADER_AUTH": value}).enabled)

    def test_a_typo_is_an_error_not_a_silent_disable(self):
        """Falling back to the login form would look like a working deploy."""
        with self.assertRaises(TrustedHeaderConfigError):
            loadTrustedHeaderConfig({"TRUSTED_HEADER_AUTH": "ture"})

    def test_enabled_without_cidrs_refuses_to_start(self):
        with self.assertRaises(TrustedHeaderConfigError):
            loadTrustedHeaderConfig({"TRUSTED_HEADER_AUTH": "1"})

    def test_enabled_with_empty_cidrs_refuses_to_start(self):
        with self.assertRaises(TrustedHeaderConfigError):
            loadTrustedHeaderConfig({"TRUSTED_HEADER_AUTH": "1",
                                     "TRUSTED_PROXY_CIDRS": "  , "})

    def test_a_malformed_cidr_refuses_to_start(self):
        with self.assertRaises(TrustedHeaderConfigError):
            loadTrustedHeaderConfig({"TRUSTED_HEADER_AUTH": "1",
                                     "TRUSTED_PROXY_CIDRS": "not-a-network"})

    def test_multiple_networks_are_parsed(self):
        config = loadTrustedHeaderConfig(TRUSTED_ENV)
        self.assertTrue(config.enabled)
        self.assertEqual([str(n) for n in config.networks],
                         ["10.0.0.5/32", "192.168.1.0/24"])


class PeerCheckTestCase(unittest.TestCase):
    def setUp(self):
        self.config = loadTrustedHeaderConfig(TRUSTED_ENV)

    def test_address_inside_a_range_is_trusted(self):
        self.assertTrue(isTrustedPeer(PROXY_PEER, self.config.networks))
        self.assertTrue(isTrustedPeer("192.168.1.20", self.config.networks))

    def test_address_outside_every_range_is_not(self):
        self.assertFalse(isTrustedPeer(OTHER_PEER, self.config.networks))

    def test_ipv4_mapped_ipv6_is_folded(self):
        self.assertTrue(isTrustedPeer("::ffff:10.0.0.5", self.config.networks))

    def test_empty_or_garbage_is_never_trusted(self):
        for value in ("", "unknown", "10.0.0.5/32"):
            with self.subTest(value=value):
                self.assertFalse(isTrustedPeer(value, self.config.networks))

    def test_peer_address_prefers_proxyfix_saved_original(self):
        environ = {
            "REMOTE_ADDR": "203.0.113.9",   # what X-Forwarded-For said
            "werkzeug.proxy_fix.orig": {"REMOTE_ADDR": PROXY_PEER},
        }
        self.assertEqual(peerAddress(environ), PROXY_PEER)

    def test_peer_address_falls_back_when_proxyfix_did_not_run(self):
        self.assertEqual(peerAddress({"REMOTE_ADDR": PROXY_PEER}), PROXY_PEER)
        self.assertEqual(peerAddress({}), "")


class IdentityTestCase(unittest.TestCase):
    def setUp(self):
        self.config = loadTrustedHeaderConfig(TRUSTED_ENV)

    def test_remote_user_alone_gets_a_stable_synthetic_email(self):
        identity = identityFromHeaders({"Remote-User": "myrddin"}, self.config)
        self.assertIsNotNone(identity)
        self.assertEqual(identity.user, "myrddin")
        self.assertEqual(identity.email,
                         f"myrddin@{SYNTHETIC_EMAIL_DOMAIN}")

    def test_remote_email_is_the_account_key(self):
        identity = identityFromHeaders(
            {"Remote-User": "myrddin", "Remote-Email": "Myrddin@Emrys.Dev"},
            self.config)
        self.assertEqual(identity.email, "myrddin@emrys.dev")

    def test_missing_or_malformed_user_is_refused(self):
        for headers in ({}, {"Remote-User": ""}, {"Remote-User": "two words"},
                        {"Remote-User": "bad@name"}):
            with self.subTest(headers=headers):
                self.assertIsNone(identityFromHeaders(headers, self.config))

    def test_malformed_email_is_refused(self):
        self.assertIsNone(identityFromHeaders(
            {"Remote-User": "myrddin", "Remote-Email": "not-an-email"},
            self.config))


class TrustedHeaderRequestTestCase(unittest.TestCase):
    def _makeApp(self, env=None):
        values = dict(TRUSTED_ENV)
        if env:
            values.update(env)
        with patch.dict(os.environ, values, clear=False):
            dash = makeApp()
        self.addCleanup(dash.shutdown)
        return dash

    def _get(self, dash, path="/", peer=PROXY_PEER, headers=None, **kwargs):
        return dash.app.test_client().get(
            path, headers=headers or {}, environ_base={"REMOTE_ADDR": peer},
            **kwargs)

    def test_first_header_login_creates_the_account(self):
        dash = self._makeApp()
        # A path with no view: the request only has to pass the before_request
        # hook for the account to be created, and 404 keeps this test out of a
        # dashboard render that would start a real per-user Database.
        response = self._get(dash, path="/no-such-page",
                             headers={"Remote-User": "myrddin"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(dash.repo.getUsernameForEmail(
            f"myrddin@{SYNTHETIC_EMAIL_DOMAIN}"), "myrddin")

    def test_a_request_without_the_header_is_refused(self):
        dash = self._makeApp()
        response = self._get(dash)

        self.assertEqual(response.status_code, 403)
        self.assertIn("did not supply an identity",
                      response.get_data(as_text=True))

    def test_a_header_from_an_untrusted_peer_is_refused(self):
        dash = self._makeApp()
        response = self._get(dash, peer=OTHER_PEER,
                             headers={"Remote-User": "myrddin"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("only reachable through its authenticating proxy",
                      response.get_data(as_text=True))

    def test_a_forged_x_forwarded_for_cannot_move_an_untrusted_peer_in(self):
        """ProxyFix rewrites REMOTE_ADDR; the allowlist must use the socket peer.

        Without TRUST_PROXY_HEADERS the header is ignored. With it, ProxyFix
        saves the original - both configurations are exercised here because
        the security claim is about the real peer in both.
        """
        for proxyHops in (None, "1"):
            with self.subTest(trust_proxy_headers=proxyHops):
                env = {"TRUST_PROXY_HEADERS": proxyHops} if proxyHops else {}
                dash = self._makeApp(env=env)
                response = self._get(
                    dash, peer=OTHER_PEER,
                    headers={"Remote-User": "myrddin",
                             "X-Forwarded-For": PROXY_PEER})
                self.assertEqual(response.status_code, 403)

    def test_a_trusted_peer_is_still_trusted_when_xff_names_someone_else(self):
        dash = self._makeApp(env={"TRUST_PROXY_HEADERS": "1"})
        response = self._get(
            dash, path="/no-such-page",
            headers={"Remote-User": "myrddin",
                     "X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(dash.repo.getUsernameForEmail(
            f"myrddin@{SYNTHETIC_EMAIL_DOMAIN}"), "myrddin")

    def test_the_app_login_form_is_unreachable(self):
        dash = self._makeApp()
        headers = {"Remote-User": "myrddin"}
        for path in ("/login", "/register", "/reset-password"):
            with self.subTest(path=path):
                response = self._get(dash, path=path, headers=headers)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('name="password"', response.get_data(as_text=True))

    def test_health_and_static_assets_stay_open_for_probes(self):
        dash = self._makeApp()
        static = self._get(dash, path="/static/css/style.css", peer=OTHER_PEER)
        self.assertEqual(static.status_code, 200)
        # /health has no identity header and its peer is the node, not the proxy.
        health = self._get(dash, path="/health", peer=OTHER_PEER)
        self.assertEqual(health.status_code, 200)

    def test_a_header_session_survives_a_dead_spotify_listener(self):
        """Authentication is the proxy's; a dead listener is not a logout.

        get_current_user_or_redirect() used to require is_user_logged_in(),
        which is false without live cookies - exactly the first-login state.
        In header mode it must accept the session regardless, or every page
        would redirect to a /login that header mode serves as 404.
        """
        dash = self._makeApp()
        with dash.app.test_request_context("/"):
            flaskSession["email"] = "myrddin@emrys.dev"
            flaskSession["username"] = "myrddin"
            flaskSession[TRUSTED_HEADER_SESSION_KEY] = True
            flaskSession[SESSION_VERSION_KEY] = 0
            with patch.object(dash, "get_username_for_email", return_value="myrddin"), \
                 patch.object(dash, "get_user_db", return_value=MagicMock()), \
                 patch.object(dash, "is_user_logged_in",
                              side_effect=AssertionError("must not be consulted")):
                email, username, _db = dash.get_current_user_or_redirect()

        self.assertEqual((email, username), ("myrddin@emrys.dev", "myrddin"))

    def test_a_standalone_session_still_requires_a_live_spotify_listener(self):
        dash = self._makeApp(env={})   # header mode is still on - but see below
        # A session that is NOT marked as header-established is the standalone
        # path even on a header-mode instance (e.g. a cookie minted before the
        # mode was enabled), so the old rule still applies to it.
        with dash.app.test_request_context("/"):
            flaskSession["email"] = "old@example.com"
            flaskSession["username"] = "old"
            with patch.object(dash, "get_username_for_email", return_value="old"), \
                 patch.object(dash, "get_user_db", return_value=MagicMock()), \
                 patch.object(dash, "is_user_logged_in", return_value=False):
                email, username, _db = dash.get_current_user_or_redirect()

        self.assertEqual((email, username), (None, None))

    def test_enabling_without_a_proxy_allowlist_refuses_to_construct(self):
        with patch.dict(os.environ, {"TRUSTED_HEADER_AUTH": "1",
                                     "TRUSTED_PROXY_CIDRS": ""}, clear=False):
            with self.assertRaises(TrustedHeaderConfigError):
                makeApp()


if __name__ == "__main__":
    unittest.main()
