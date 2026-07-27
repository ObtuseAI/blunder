from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from blunder.fund.bootstrap import RolePublicKeys, build_fund_application, make_loopback_server
from blunder.fund.crypto import Ed25519Signer


REPO_ROOT = Path(__file__).resolve().parents[1]


class FundBootstrapTests(unittest.TestCase):
    def test_injected_application_and_authenticated_loopback_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = Ed25519Signer.generate()
            operator = Ed25519Signer.generate()
            application = build_fund_application(
                data_root=Path(directory),
                policy_path=REPO_ROOT / "configs" / "dumbmoney" / "risk-policy.v1.json",
                core_signer=core,
                role_public_keys=RolePublicKeys(operator=(operator.public_key_bytes,)),
            )
            token = "test-token-with-adequate-entropy-0123456789"
            desktop_token = "desktop-read-token-with-adequate-entropy-1234567890"
            cell_token = "dummy-cell-token-with-adequate-entropy-9876543210"
            server = make_loopback_server(
                application,
                port=0,
                operator_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                desktop_read_token_sha256=hashlib.sha256(
                    desktop_token.encode()
                ).hexdigest(),
                cell_token_sha256={
                    "dummy_kalshi": hashlib.sha256(cell_token.encode()).hexdigest(),
                },
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                self.assertEqual(host, "127.0.0.1")
                connection = http.client.HTTPConnection(host, port, timeout=5)
                connection.request("GET", "/health/live")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.request("GET", "/api/v1/fund/control-snapshot")
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                connection.request(
                    "GET",
                    "/api/v1/fund/control-snapshot",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read())
                self.assertEqual(payload["schema"], "dumbmoney.control-snapshot.v1")
                self.assertEqual(payload["status"], "PAUSED")
                connection.request(
                    "GET",
                    "/api/v1/fund/control-snapshot",
                    headers={"X-Blunder-Token": desktop_token},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    json.loads(response.read())["schema"],
                    "dumbmoney.control-snapshot.v1",
                )
                connection.request(
                    "GET",
                    (
                        f"/v1/cells/dummy_kalshi/commands?after=0&cursor={'0' * 64}"
                        f"&request_nonce={'a' * 64}"
                    ),
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                connection.request(
                    "GET",
                    (
                        f"/v1/cells/dummy_kalshi/commands?after=0&cursor={'0' * 64}"
                        f"&request_nonce={'b' * 64}"
                    ),
                    headers={"Authorization": f"Bearer {cell_token}"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                command_page = json.loads(response.read())
                self.assertEqual(command_page["request_nonce"], "b" * 64)
                self.assertEqual(command_page["required_action"], "CANCEL_AND_RECONCILE")
                contract_path = (
                    f"/v1/cells/dummy_kalshi/contracts/{'c' * 64}"
                    f"?capital_envelope_digest={'e' * 64}"
                    f"&request_nonce={'d' * 64}"
                )
                connection.request(
                    "GET",
                    contract_path,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                connection.request(
                    "GET",
                    contract_path,
                    headers={"Authorization": f"Bearer {cell_token}"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                application.close()

    def test_core_key_cannot_be_injected_as_operator_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = Ed25519Signer.generate()
            with self.assertRaisesRegex(ValueError, "core capital signer.*operator"):
                build_fund_application(
                    data_root=Path(directory),
                    policy_path=REPO_ROOT / "configs" / "dumbmoney" / "risk-policy.v1.json",
                    core_signer=core,
                    role_public_keys=RolePublicKeys(operator=(core.public_key_bytes,)),
                )


if __name__ == "__main__":
    unittest.main()
