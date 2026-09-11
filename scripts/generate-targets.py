"""Render private Prometheus targets using Docker Compose's .env interpolation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def generate(root):
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        cwd=root, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError("Compose 설정을 읽지 못했습니다. .env의 필수 설정을 확인하세요.")
    settings = json.loads(result.stdout)["x-prometheus-targets"]
    targets = []
    for name in ("icpm", "pcpm"):
        value = settings[name]
        if not isinstance(value, str) or not value or any(c in value for c in "/@?# \t\r\n"):
            raise ValueError("수집 대상은 scheme과 경로가 없는 host:port 형식이어야 합니다.")
        targets.append({"targets": [value], "labels": {
            "service": "caddy_proxy_manager", "cpm_instance": name, "instance": name.upper(),
        }})
    directory = root / "prometheus/cpm-targets"
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix=".cpm-", suffix=".tmp")
    temp = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(targets, stream, indent=2)
            stream.write("\n")
        # The unprivileged Prometheus container must be able to read these addresses.
        temp.chmod(0o644)
        temp.replace(directory / "cpm.json")
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        generate(Path(__file__).resolve().parent.parent)
    except Exception:
        raise SystemExit("타깃 생성 실패: Docker Compose와 .env의 필수 설정을 확인하세요.")
    print("Prometheus 수집 대상 파일을 생성했습니다.")
