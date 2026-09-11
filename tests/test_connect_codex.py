import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import tomlkit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("connect_codex", SCRIPTS / "connect-codex.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
from tailnet import candidates, ipv4


def inventory():
    return {"Self": {"HostName": "pc", "TailscaleIPs": ["100.64.0.1"]}, "Peer": {
        "a": {"DNSName": "grafana.example.ts.net.", "TailscaleIPs": ["100.64.0.2"]},
        "b": {"HostName": "other", "TailscaleIPs": ["100.64.0.3"]},
        "c": {"HostName": "public", "TailscaleIPs": ["192.0.2.1"]},
    }}


class DiscoveryTests(unittest.TestCase):
    def test_real_http_discovery_and_unrelated_service(self):
        class Handler(BaseHTTPRequestHandler):
            marker = {"service": "grafana-telemetry", "version": 1, "transport": "otlp-http"}

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                if self.path == "/.well-known/grafana-telemetry":
                    self.wfile.write(json.dumps(self.marker).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(client, "PORT", server.server_port):
                self.assertEqual(client.probe("127.0.0.1"), f"http://127.0.0.1:{server.server_port}")
                Handler.marker = {"service": "something-else"}
                self.assertIsNone(client.probe("127.0.0.1"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_inventory_only_uses_tailnet_addresses(self):
        self.assertEqual(set(candidates(inventory())), {"100.64.0.1", "100.64.0.2", "100.64.0.3"})
        self.assertIsNone(ipv4({"TailscaleIPs": ["bad", "127.0.0.1", "192.168.0.1"]}))

    def test_server_name_is_exact_not_substring(self):
        self.assertEqual(list(candidates(inventory(), "GRAFANA")), ["100.64.0.2"])
        self.assertEqual(candidates(inventory(), "graf"), {})

    @patch.object(client, "probe", side_effect=lambda address: "http://100.64.0.2:14318" if address.endswith(".2") else None)
    def test_discovers_unique_server(self, probe):
        self.assertEqual(client.discover(inventory()), "http://100.64.0.2:14318")

    @patch.object(client, "probe", return_value=None)
    def test_no_server_leaves_config_untouched(self, probe):
        with self.assertRaisesRegex(RuntimeError, "찾지 못"):
            client.discover(inventory())

    @patch.object(client, "probe", side_effect=lambda address: f"http://{address}:14318")
    def test_ambiguous_servers_require_selection(self, probe):
        with self.assertRaisesRegex(RuntimeError, "여러 대"):
            client.discover(inventory())

    def test_redirect_is_not_followed(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com"))

    @patch("urllib.request.build_opener")
    def test_marker_is_not_enough_without_collector_readiness(self, opener):
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"service": "grafana-telemetry", "version": 1, "transport": "otlp-http"}
        ).encode()
        opener.return_value.open.return_value.__enter__.return_value.status = 503
        self.assertIsNone(client.probe("100.64.0.2"))


class ConfigTests(unittest.TestCase):
    endpoint = "http://100.64.0.2:14318"
    original = '''# personal config
model = "test-model" # keep this
[mcp_servers.example]
command = "example"
[otel]
environment = "existing-env"
log_user_prompt = true
exporter = { otlp-http = { endpoint = "https://old/v1/logs", headers = { Authorization = "secret" } } }
[profiles.work]
model = "work-model"
'''

    def test_preserves_unrelated_tables_and_comments(self):
        result = client.render(self.original, self.endpoint)
        parsed = tomlkit.parse(result)
        self.assertIn("# personal config", result)
        self.assertIn("# keep this", result)
        self.assertEqual(parsed["mcp_servers"]["example"]["command"], "example")
        self.assertEqual(parsed["profiles"]["work"]["model"], "work-model")
        self.assertEqual(parsed["otel"]["environment"], "existing-env")
        self.assertFalse(parsed["otel"]["log_user_prompt"])
        self.assertNotIn("secret", result)
        for key, signal in (("exporter", "logs"), ("metrics_exporter", "metrics"), ("trace_exporter", "traces")):
            self.assertEqual(parsed["otel"][key]["otlp-http"]["endpoint"], self.endpoint + "/v1/" + signal)

    def test_idempotent_render(self):
        result = client.render(self.original, self.endpoint)
        self.assertEqual(client.render(result, self.endpoint), result)

    def test_inline_otel_and_nested_exporter_forms(self):
        for original in ('otel = { environment = "pc" }\n', '[otel.exporter.otlp-http]\nendpoint = "http://old"\n'):
            result = client.render(original, self.endpoint)
            self.assertEqual(tomlkit.parse(result)["otel"]["exporter"]["otlp-http"]["endpoint"], self.endpoint + "/v1/logs")

    def test_backup_and_idempotent_file_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(self.original, encoding="utf-8")
            outcome, backup = client.configure(path, self.endpoint)
            self.assertEqual(outcome, "updated")
            self.assertEqual(backup.read_text(encoding="utf-8"), self.original)
            self.assertEqual(client.configure(path, self.endpoint), ("unchanged", None))
            self.assertEqual(len(list(Path(directory).glob("config.toml.backup-*"))), 1)

    def test_dry_run_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new/config.toml"
            self.assertEqual(client.configure(path, self.endpoint, True), ("dry-run", None))
            self.assertFalse(path.parent.exists())

    def test_invalid_toml_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[invalid", encoding="utf-8")
            with self.assertRaises(Exception):
                client.configure(path, self.endpoint)
            self.assertEqual(path.read_text(), "[invalid")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_lock_prevents_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.with_name("config.toml.telemetry.lock").touch()
            with self.assertRaisesRegex(RuntimeError, "잠금"):
                client.configure(path, self.endpoint)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
