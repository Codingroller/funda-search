"""E2E test fixtures — spins up a real uvicorn server for Playwright tests."""
import os
import socket
import subprocess
import tempfile
import time

import pytest

# Real VAPID keypair generated for tests (not production)
_TEST_VAPID_PUB = "BK0zzGjpUNfW_PyuJHQ0Z84ckLDR-lSDreqRIsOvxaeJA_FlQw0OWDsAbbJvsJp3zEi_carJLJs358wZnHbTXRg"
_TEST_VAPID_PRIV = "MHcCAQEEIMH3hSbJwNbAsmcwmqcnFZnSUbIc8d-pS5jdNfl8UEh5oAoGCCqGSM49AwEHoUQDQgAErTPMaOlQ19b8_K4kdDRnzhyQsNH6VIOt6pEiw6_Fp4kD8WVDDQ5YOwBtsm-wmnfMSL9xqsksmzfnzBmcdtNdGA"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LiveServer(str):
    """String subclass so f"{live_server}/path" still works, but db_path is accessible."""
    db_path: str

    def __new__(cls, url: str, db_path: str):
        obj = super().__new__(cls, url)
        obj.db_path = db_path
        return obj


@pytest.fixture(scope="session")
def live_server():
    port = _free_port()
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "SECRET_KEY": "e2e-test-secret-key-padded-to-32-chars!!",
        "ADMIN_PASSWORD": "e2etestpass",
        "ADMIN_USERNAME": "e2eadmin",
        "DB_PATH": db_file.name,
        "HTTPS_ONLY": "false",
        "VAPID_PUBLIC_KEY": _TEST_VAPID_PUB,
        "VAPID_PRIVATE_KEY": _TEST_VAPID_PRIV,
        "VAPID_SUBJECT": "mailto:test@example.com",
    }

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    venv_uvicorn = os.path.join(project_root, ".venv", "bin", "uvicorn")
    proc = subprocess.Popen(
        [venv_uvicorn, "app.main:app", "--host", "127.0.0.1", f"--port={port}"],
        env=env,
        cwd=project_root,
    )
    # Wait for the server to be ready
    for _ in range(30):
        time.sleep(0.5)
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
            break
        except Exception:
            continue

    yield _LiveServer(f"http://127.0.0.1:{port}", db_file.name)

    proc.terminate()
    proc.wait()
    os.unlink(db_file.name)


@pytest.fixture(scope="session")
def auth_page(live_server, browser):
    """Returns a Playwright page already logged in as the admin user."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{live_server}/login")
    page.fill('input[name="username"]', "e2eadmin")
    page.fill('input[name="password"]', "e2etestpass")
    with page.expect_navigation(timeout=10000):
        page.click('button[type="submit"]')
    # Confirm we landed past the login screen
    assert "/login" not in page.url, f"Login failed, stuck at {page.url}"
    return page
