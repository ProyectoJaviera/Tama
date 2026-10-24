import os
import subprocess
import sys


def run_import(keylog: str) -> str:
    env = {**os.environ, "SSLKEYLOGFILE": keylog}
    code = "import os, tama; print(os.environ.get('SSLKEYLOGFILE', ''))"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    return out.stdout.strip()


def test_device_keylog_is_removed():
    assert run_import(r"\\.\fakeDriverProxy\123") == ""


def test_regular_keylog_file_is_kept(tmp_path):
    path = str(tmp_path / "keys.log")
    assert run_import(path) == path
