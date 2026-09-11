"""Local Tailscale inventory, shared by the server and personal-PC setup tools."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess


def tailscale_binary():
    candidates = [
        shutil.which("tailscale"),
        str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale/tailscale.exe"),
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/usr/local/bin/tailscale",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Tailscale CLI를 찾지 못했습니다. Tailscale을 설치하고 로그인하세요.")


def status():
    result = subprocess.run(
        [tailscale_binary(), "status", "--json"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise RuntimeError("Tailscale 상태를 읽지 못했습니다. 앱이 실행 중이고 로그인되어 있는지 확인하세요.")
    data = json.loads(result.stdout)
    if data.get("BackendState") != "Running":
        raise RuntimeError("Tailscale 연결이 꺼져 있습니다. 연결 후 다시 실행하세요.")
    return data


def ipv4(node):
    for value in node.get("TailscaleIPs", []):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10"):
            return str(address)
    return None


def candidates(data, server=None):
    """Only daemon-authenticated tailnet addresses; never subnet/public endpoints."""
    nodes = [data.get("Self", {}), *data.get("Peer", {}).values()]
    result = {}
    for node in nodes:
        address = ipv4(node)
        if not address:
            continue
        dns = node.get("DNSName", "").rstrip(".")
        names = {address, dns, dns.split(".")[0], node.get("HostName", "")}
        if server and server.rstrip(".").lower() not in {n.lower() for n in names}:
            continue
        result[address] = dns or node.get("HostName") or address
    if len(result) > 256:
        raise RuntimeError("기기가 256대를 초과합니다. --server <Tailscale 기기 이름>으로 범위를 지정하세요.")
    return result
