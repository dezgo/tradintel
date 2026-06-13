"""Test bootstrap.

Point the SQLite singleton at a throwaway database BEFORE any app module is
imported, so tests never touch (or pollute) the real trading.db. app.storage
captures the DB path at import time from the BOT_DB env var, so this must run
first — which it does, since conftest is imported before the test modules.
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="tradintel_test_")
os.environ["BOT_DB"] = os.path.join(_tmpdir, "test.db")
