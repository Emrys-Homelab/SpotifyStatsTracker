"""The Spotify account is tracked, not used as a login check.

The login identity and the Spotify account are decoupled (the trusted-header
proxy decides who may use the app; the cookies decide whose listening is read).
So a Spotify account whose email differs from the account email is the NORMAL
state of a one-user SSO deployment, not contamination: the listener must record
it, name it, and carry on. What is still worth noticing is a CHANGE of Spotify
account mid-session - that stays a warning and an annotation, never a refusal.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if isinstance(sys.modules.get("Database.database"), MagicMock):
    del sys.modules["Database.database"]

from conftest import DatabaseTestCase
from Database.Listeners.spotifyListener import (
    Listener,
    USER_VALIDATION_CACHE_SECONDS,
)

_MONOTONIC = "Database.Listeners.spotifyListener.time.monotonic"
_T0 = 100_000.0
_ONE_SECOND = 1.0


def _makeListener(cookieAccountEmail, expectedEmail="expected@example.com",
                  displayName=None, listenerKwargs=None):
    """A real Listener constructed against a mocked Spotify client whose
    session authenticates as `cookieAccountEmail`."""
    sp = MagicMock()
    sp.current_user.return_value = {
        "id": "spotify-user-1",
        "email": cookieAccountEmail,
        "display_name": displayName,
    }
    sp.current_user_recently_played.return_value = []
    sp.isLoggedIn.return_value = True
    kwargs = dict(listenerKwargs or {})
    with patch("Database.Listeners.spotifyListener.Spotify", return_value=sp):
        listener = Listener(cookiesFile="unused.json", email=expectedEmail, **kwargs)
    return listener, sp


def _listenerWhoseBuildTimeCheckRaised(expectedEmail="expected@example.com"):
    """A real Listener whose __init__ identity check met a bot-check page:
    no baseline at build time, so _validateCurrentUser's first answer adopts
    one."""
    sp = MagicMock()
    sp.current_user.side_effect = RuntimeError(
        "Invalid JSON (Status: 200, Type: str, Response: <html>Oh nein!</html>)")
    sp.current_user_recently_played.return_value = []
    sp.isLoggedIn.return_value = True
    with patch("Database.Listeners.spotifyListener.Spotify", return_value=sp):
        listener = Listener(cookiesFile="unused.json", email=expectedEmail)
    sp.current_user.reset_mock()
    sp.current_user.side_effect = None
    return listener, sp


class TestBaselineRecordsTheObservedAccount(unittest.TestCase):
    def test_a_mismatched_account_is_recorded_not_flagged(self):
        listener, _ = _makeListener("someone-else@example.com",
                                    displayName="Someone Else")
        self.assertTrue(listener._captureIdentityBaseline(
            {"id": "spotify-user-1", "email": "someone-else@example.com",
             "display_name": "Someone Else"}))
        self.assertEqual(listener.spotify_account_email, "someone-else@example.com")
        self.assertEqual(listener.spotify_account_name, "Someone Else")
        self.assertFalse(getattr(listener, "contaminationDetected", False))
        self.assertFalse(listener.accountChanged)

    def test_the_init_baseline_reports_the_identity_to_the_callback(self):
        observed = []
        _makeListener("someone-else@example.com", displayName="Someone Else",
                      listenerKwargs={"on_identity_observed": observed.append})
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["email"], "someone-else@example.com")
        self.assertEqual(observed[0]["name"], "Someone Else")

    def test_email_comparison_is_case_insensitive_and_not_a_mismatch(self):
        listener, _ = _makeListener("Expected@Example.com")
        self.assertEqual(listener.spotify_account_email, "Expected@Example.com")
        self.assertFalse(listener.accountChanged)

    def test_no_expected_email_is_still_recorded(self):
        """Manual/dev construction without a login email still learns the
        Spotify account - that is the whole point of recording it."""
        listener, _ = _makeListener("whoever@example.com", expectedEmail=None)
        self.assertEqual(listener.spotify_account_email, "whoever@example.com")
        self.assertFalse(listener.accountChanged)

    def test_profile_without_email_does_not_clobber_a_recorded_one(self):
        listener, _ = _makeListener("known@example.com")
        listener._captureIdentityBaseline({"id": "spotify-user-1", "email": None})
        self.assertEqual(listener.spotify_account_email, "known@example.com")
        self.assertFalse(listener.accountChanged)

    def test_profile_fetch_failure_leaves_no_baseline_but_does_not_raise(self):
        sp = MagicMock()
        sp.current_user.side_effect = RuntimeError("network down")
        sp.current_user_recently_played.return_value = []
        with patch("Database.Listeners.spotifyListener.Spotify", return_value=sp):
            listener = Listener(cookiesFile="unused.json", email="expected@example.com")
        self.assertIsNone(listener._authenticated_user_id)
        listener, _ = _listenerWhoseBuildTimeCheckRaised()
        self.assertIsNone(listener._authenticated_user_id)


class TestNoRefusalOnMismatch(unittest.TestCase):
    def test_a_mismatched_account_reports_logged_in(self):
        """The old behaviour returned False here, routing the user back to a
        login page that no longer checks the pair."""
        listener, _ = _makeListener("intruder@example.com")
        self.assertTrue(listener.isLoggedIn())

    def test_startListener_enters_the_poll_loop(self):
        """It must not return before polling: a mismatch is not a refusal."""
        listener, _ = _makeListener("intruder@example.com")
        with patch.object(listener, "_checkOnce", return_value=False) as checkOnce:
            listener.startListener(MagicMock())
        checkOnce.assert_called_once()


class TestMidSessionAccountChange(unittest.TestCase):
    def test_a_changed_account_is_an_annotation_not_a_refusal(self):
        observed = []
        listener, sp = _makeListener("first@example.com",
                                     listenerKwargs={"on_identity_observed": observed.append})
        observed.clear()
        sp.current_user.return_value = {"id": "second-user", "email": "second@example.com",
                                        "display_name": "Second"}
        with patch(_MONOTONIC, return_value=_T0), \
             self.assertLogs("Database.Listeners.spotifyListener", level="WARNING") as logs:
            self.assertTrue(listener._validateCurrentUser())

        self.assertTrue(listener.accountChanged)
        self.assertEqual(listener._authenticated_user_id, "second-user")
        self.assertEqual(listener.spotify_account_email, "second@example.com")
        self.assertEqual(observed[-1]["email"], "second@example.com")
        self.assertIn("changed mid-session", "\n".join(logs.output))

    def test_the_new_account_becomes_the_baseline_for_later_polls(self):
        listener, sp = _makeListener("first@example.com")
        sp.current_user.return_value = {"id": "second-user", "email": "second@example.com"}
        with patch(_MONOTONIC, return_value=_T0), \
             self.assertLogs("Database.Listeners.spotifyListener", level="WARNING"):
            self.assertTrue(listener._validateCurrentUser())
        # The same answer next poll is no longer a change.
        with patch(_MONOTONIC, return_value=_T0 + USER_VALIDATION_CACHE_SECONDS + _ONE_SECOND):
            self.assertTrue(listener._validateCurrentUser())
        self.assertTrue(listener.accountChanged)

    def test_the_build_time_failure_still_adopts_its_first_answer(self):
        listener, sp = _listenerWhoseBuildTimeCheckRaised()
        sp.current_user.return_value = {"id": "spotify-user-1",
                                        "email": "someone-else@example.com"}
        with patch(_MONOTONIC, return_value=_T0):
            self.assertTrue(listener._validateCurrentUser())
        self.assertEqual(listener._authenticated_user_id, "spotify-user-1")
        self.assertEqual(listener.spotify_account_email, "someone-else@example.com")


class TestListenerHealth(DatabaseTestCase):
    def _startWithListener(self, listener):
        db = self._makeDb({}, [])
        with patch("Database.database.Listener", return_value=listener):
            db.startListener(email="expected@example.com")
        return db

    def test_a_changed_account_stays_healthy_and_records_the_annotation(self):
        listener = MagicMock()
        listener.loginFailed = False
        listener.accountChanged = True
        listener.spotify_account_email = "new@example.com"

        db = self._startWithListener(listener)

        health = db.getListenerHealth()
        self.assertEqual(health["status"], "HEALTHY")
        self.assertIn("Spotify account changed", health["last_error"])
        self.assertIn("new@example.com", health["last_error"])
        listener.startListener_thread.assert_called_once()

    def test_a_login_failure_is_dead_and_not_started(self):
        listener = MagicMock()
        listener.loginFailed = True
        listener.accountChanged = False

        db = self._startWithListener(listener)

        health = db.getListenerHealth()
        self.assertEqual(health["status"], "DEAD")
        self.assertIn("login failed", health["last_error"])
        listener.startListener_thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
