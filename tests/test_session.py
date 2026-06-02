"""Tests for session persistence."""

import pytest

from webmcp_gen import session


@pytest.fixture(autouse=True)
def temp_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBMCP_CACHE_DIR", str(tmp_path))
    yield tmp_path


SAMPLE_STATE = {
    "cookies": [{"name": "auth", "value": "secret", "domain": "x.com", "path": "/"}],
    "origins": [{"origin": "https://x.com", "localStorage": [{"name": "k", "value": "v"}]}],
}


class TestSessionStore:
    def test_save_and_load(self):
        session.save_session("mysite", SAMPLE_STATE)
        loaded = session.load_session("mysite")
        assert loaded == SAMPLE_STATE

    def test_load_missing_returns_none(self):
        assert session.load_session("nope") is None

    def test_list_sessions(self):
        session.save_session("a", SAMPLE_STATE)
        session.save_session("b", SAMPLE_STATE)
        assert set(session.list_sessions()) == {"a", "b"}

    def test_delete_session(self):
        session.save_session("a", SAMPLE_STATE)
        assert session.delete_session("a") is True
        assert session.load_session("a") is None
        assert session.delete_session("a") is False

    def test_name_sanitization(self):
        # Names with slashes / special chars shouldn't escape the sessions dir
        session.save_session("../../evil name!", SAMPLE_STATE)
        sessions = session.list_sessions()
        assert len(sessions) == 1
        assert "/" not in sessions[0]

    def test_corrupt_session_returns_none(self, temp_sessions):
        path = session.session_path("broken")
        path.write_text("not json {{{")
        assert session.load_session("broken") is None

    def test_saved_file_permissions_restrictive(self, temp_sessions):
        import os
        import stat
        session.save_session("perms", SAMPLE_STATE)
        path = session.session_path("perms")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        # Should not be group/world readable (contains auth cookies)
        assert mode & 0o077 == 0
