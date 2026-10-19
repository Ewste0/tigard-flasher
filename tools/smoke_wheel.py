"""Install the built wheel in isolation and serve its bundled UI without USB."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = r'''
import json, os, threading, urllib.request
from pathlib import Path
import spc5tool
from spc5tool.web import make_server, STATIC_ASSETS
assert Path(spc5tool.__file__).resolve().is_relative_to(Path(os.environ['PYTHONPATH']).resolve())
server = make_server(Path.cwd() / 'workspace', port=0)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    base = f'http://127.0.0.1:{server.server_address[1]}'
    for route, (_, mime) in STATIC_ASSETS.items():
        with urllib.request.urlopen(base + route, timeout=5) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == mime.split(';')[0]
            assert len(response.read()) > 100
    with urllib.request.urlopen(base + '/api/status', timeout=5) as response:
        status = json.load(response)
        assert status['hardware_enabled']
        assert status['initial_mode'] == 'hardware'
    print(f'Installed wheel {spc5tool.__version__}: CLI and all {len(STATIC_ASSETS)} UI assets passed; no hardware operation requested.')
finally:
    server.shutdown()
    server.server_close()
    thread.join()
'''


def main():
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected one wheel in dist/; use a fresh build directory.")
    with tempfile.TemporaryDirectory(prefix="tigard-wheel-") as directory:
        site = Path(directory) / "site"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
                        "--target", str(site), str(wheels[0])], check=True)
        env = {**os.environ, "PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1"}
        result = subprocess.run([sys.executable, "-m", "spc5tool", "info", "mpc5748g"],
                                cwd=directory, env=env, check=True, capture_output=True, text=True)
        if "0x00f90000" not in result.stdout:
            raise AssertionError("Installed CLI did not report expected MPC5748 geometry")
        subprocess.run([sys.executable, "-c", CHECK], cwd=directory, env=env, check=True)


if __name__ == "__main__":
    main()
