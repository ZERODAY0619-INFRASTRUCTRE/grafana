# /// script
# requires-python = ">=3.11"
# dependencies = ["tomlkit==0.13.3"]
# ///
"""Discover the tailnet collector and configure Codex on THIS personal PC."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import urllib.error
import urllib.request

import tomlkit

from tailnet import candidates, status

PORT = 14318


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(address):
    base = f"http://{address}:{PORT}"
    # Do not send tailnet discovery through a system HTTP proxy or follow redirects.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with client.open(base + "/.well-known/grafana-telemetry", timeout=2) as response:
            marker = json.loads(response.read(4096))
        if marker != {"service": "grafana-telemetry", "version": 1, "transport": "otlp-http"}:
            return None
        with client.open(base + "/ready", timeout=2) as response:
            if response.status != 200:
                return None
        return base
    except (OSError, ValueError, urllib.error.URLError):
        return None


def discover(data, server=None):
    peers = candidates(data, server)
    if not peers:
        raise RuntimeError("Tailscale 목록에서 대상 기기를 찾지 못했습니다.")
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [(address, endpoint) for address, endpoint in zip(peers, pool.map(probe, peers)) if endpoint]
    if not found:
        raise RuntimeError(
            "수집 서버를 찾지 못했습니다. 서버에서 start-telemetry.py를 실행하고 "
            "Tailscale 정책에서 PC → 서버 TCP 14318 연결을 허용하세요."
        )
    if len(found) != 1:
        names = ", ".join(peers[address] for address, _ in found)
        raise RuntimeError(f"수집 서버가 여러 대입니다: {names}. --server <기기 이름>을 지정하세요.")
    return found[0][1]


def render(original, endpoint):
    doc = tomlkit.parse(original)
    if "otel" not in doc:
        doc["otel"] = tomlkit.table()
    otel = doc["otel"]
    if not hasattr(otel, "keys"):
        raise RuntimeError("기존 otel 설정이 TOML 테이블이 아닙니다.")
    otel.setdefault("environment", "personal-pc")
    otel["log_user_prompt"] = False
    for key, signal in (("exporter", "logs"), ("metrics_exporter", "metrics"), ("trace_exporter", "traces")):
        transport = tomlkit.inline_table()
        transport.update({"endpoint": f"{endpoint}/v1/{signal}", "protocol": "binary"})
        exporter = tomlkit.inline_table()
        exporter["otlp-http"] = transport
        otel[key] = exporter
    rendered = tomlkit.dumps(doc)
    tomlkit.parse(rendered)
    return rendered


def configure(path, endpoint, dry_run=False):
    if path.is_symlink():
        raise RuntimeError("config.toml이 심볼릭 링크입니다. 실제 파일을 --config로 지정하세요.")
    if dry_run:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        render(original, endpoint)
        return "dry-run", None
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".telemetry.lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"설정 잠금 파일이 있습니다: {lock}. 다른 연결 스크립트가 실행 중인지 확인하세요.") from exc
    os.close(lock_fd)
    temp = None
    try:
        original_bytes = path.read_bytes() if path.exists() else None
        original = original_bytes.decode("utf-8") if original_bytes is not None else ""
        rendered = render(original, endpoint)
        if rendered == original:
            return "unchanged", None
        backup = None
        if original_bytes is not None:
            fd, name = tempfile.mkstemp(prefix=path.name + ".backup-", dir=path.parent)
            backup = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(original_bytes)
        fd, name = tempfile.mkstemp(prefix=".codex-telemetry-", dir=path.parent)
        temp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        # Detect edits by Codex/an editor that does not use our lock.
        current = path.read_bytes() if path.exists() else None
        if current != original_bytes or path.is_symlink():
            raise RuntimeError("설정 파일이 실행 중 변경되었습니다. 다시 실행하세요.")
        if path.exists() and os.name != "nt":
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode) & 0o600)
        os.replace(temp, path)
        return "updated", backup
    finally:
        if temp and temp.exists():
            temp.unlink()
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", help="여러 서버 중 선택할 Tailscale 기기 이름 또는 IP")
    parser.add_argument("--dry-run", action="store_true", help="검색 및 설정 검증만 수행")
    codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--config", type=Path, default=codex_dir / "config.toml")
    args = parser.parse_args()
    try:
        endpoint = discover(status(), args.server)
        result, backup = configure(args.config.expanduser(), endpoint, args.dry_run)
        print(f"수집 서버: {endpoint}")
        print(f"Codex 설정: {args.config} ({result})")
        if backup:
            print(f"기존 설정 백업: {backup}")
        if not args.dry_run:
            print("Codex를 완전히 종료한 뒤 다시 실행하세요. 이후 사용부터 수집됩니다.")
    except Exception as exc:
        print(f"연결 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
