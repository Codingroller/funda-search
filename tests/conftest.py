import os
import tempfile

# Must be set at module level, before any app module is imported.
# pytest processes conftest.py before collecting test files, so these
# env vars are in place when app/config.py first creates Settings().
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()

os.environ.setdefault("SECRET_KEY", "test-secret-key-padded-to-32-chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("NTFY_BASE_URL", "https://ntfy.example.com")
os.environ["DB_PATH"] = _tmp_db.name  # always override; never use /data/funda.db in tests
