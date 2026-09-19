from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RENDER_CONFIG = PROJECT_ROOT / "scripts" / "render_config.py"


def test_render_config_keeps_base_and_explicit_trusted_hosts(tmp_path: Path):
    output = tmp_path / "config.json"
    result = subprocess.run(
        [
            sys.executable,
            str(RENDER_CONFIG),
            "--output",
            str(output),
            "--data-dir",
            str(tmp_path / "data"),
            "--base-url",
            "http://careers.example.test",
            "--trusted-host",
            "203.0.113.10",
            "--password-stdin",
            "--force",
        ],
        input="deployment-password-123\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads(output.read_text(encoding="utf-8"))
    assert config["app"]["trusted_hosts"] == [
        "203.0.113.10",
        "careers.example.test",
        "127.0.0.1",
        "localhost",
    ]


def test_render_config_rejects_base_url_with_path(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(RENDER_CONFIG),
            "--output",
            str(tmp_path / "config.json"),
            "--data-dir",
            str(tmp_path / "data"),
            "--base-url",
            "http://careers.example.test/not-an-origin",
            "--password-stdin",
            "--force",
        ],
        input="deployment-password-123\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "must be one HTTP(S) origin" in result.stderr
