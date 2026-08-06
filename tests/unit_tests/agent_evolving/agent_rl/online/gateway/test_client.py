from __future__ import annotations

import httpx

from openjiuwen.agent_evolving.agent_rl.online.gateway.client import GatewayAPIClient


def test_download_lora_sanitizes_fallback_filename(tmp_path):
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"artifact")

    http_client = httpx.Client(transport=httpx.MockTransport(_handler))
    try:
        client = GatewayAPIClient("http://gateway.test", client=http_client)

        target = client.download_lora("../../../etc/passwd", tmp_path)
    finally:
        http_client.close()

    assert target == tmp_path / "passwd.zip"
    assert target.read_bytes() == b"artifact"


def test_download_filename_sanitizes_windows_separators():
    response = httpx.Response(200)

    filename = GatewayAPIClient._download_filename(response, default=r"..\..\evil.zip")

    assert filename == "evil.zip"


def test_download_filename_falls_back_for_parent_directory_name():
    response = httpx.Response(
        200,
        headers={"content-disposition": 'attachment; filename="../.."'},
    )

    filename = GatewayAPIClient._download_filename(response, default="safe.zip")

    assert filename == "safe.zip"
