"""Run on the Linux Grafana Docker host, which must already be on Tailscale."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from tailnet import ipv4, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compose 설정 검증만 수행")
    args = parser.parse_args()
    try:
        address = ipv4(status().get("Self", {}))
        if not address:
            raise RuntimeError("서버의 Tailscale IPv4 주소를 찾지 못했습니다.")
        env = dict(os.environ, OTEL_TAILSCALE_IP=address)
        root = Path(__file__).resolve().parent.parent
        if not args.check:
            subprocess.run([sys.executable, str(root / "scripts/generate-targets.py")], check=True)
        command = ["docker", "compose", "-f", "compose.yml", "-f", "compose.telemetry.yml"]
        subprocess.run(command + ["config", "--quiet"], cwd=root, env=env, check=True)
        if not args.check:
            subprocess.run(command + ["up", "-d"], cwd=root, env=env, check=True)
        print(f"수집 주소: http://{address}:14318 (Tailscale 전용)")
        print("개인 PC에서 scripts/connect-codex.py를 실행하세요.")
    except Exception as exc:
        print(f"서버 설정 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
