from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _bridge.desktop_weixin_mcp_server as mcp_server  # noqa: E402


def _tool_payload(result: dict) -> dict:
    text = result["content"][0]["text"]
    return json.loads(text)


def test_tools_list_contains_current_weixin_surface():
    service = mcp_server.DesktopWeixinService()
    names = {item["name"] for item in service.tools_list({})["tools"]}
    assert "desktop_weixin.status" in names
    assert "desktop_weixin.open" in names
    assert "desktop_weixin.close" in names
    assert "desktop_weixin.chat_search" in names
    assert "desktop_weixin.message_prepare" in names
    assert "desktop_weixin.message_send_text" in names
    assert "desktop_weixin.capabilities" in names


def test_capabilities_describe_extension_contract():
    service = mcp_server.DesktopWeixinService()
    payload = _tool_payload(service.tools_call({"name": "desktop_weixin.capabilities", "arguments": {}}))
    assert payload["ok"] is True
    assert payload["extension_contract"]["no_freeform_executor"] is True
    assert payload["safety_policy"]["send_requires"]["confirm_send"] == "SEND"
    assert payload["safety_policy"]["close_requires"]["confirm_close"] == "CLOSE"


def test_send_text_refuses_without_confirmation():
    service = mcp_server.DesktopWeixinService()
    payload = _tool_payload(
        service.tools_call(
            {
                "name": "desktop_weixin.message_send_text",
                "arguments": {"text": "hello"},
            }
        )
    )
    assert payload["ok"] is False
    assert "confirm_send" in payload["error"]


def test_close_refuses_without_confirmation():
    service = mcp_server.DesktopWeixinService()
    payload = _tool_payload(service.tools_call({"name": "desktop_weixin.close", "arguments": {}}))
    assert payload["ok"] is False
    assert "confirm_close" in payload["error"]


def test_status_uses_backend_without_live_window(monkeypatch):
    monkeypatch.setattr(
        mcp_server.windows,
        "list_windows",
        lambda: [{"title": "微信", "left": 0, "top": 0, "width": 100, "height": 100, "area": 10000}],
    )
    service = mcp_server.DesktopWeixinService()
    payload = _tool_payload(service.tools_call({"name": "desktop_weixin.status", "arguments": {}}))
    assert payload["ok"] is True
    assert payload["window_count"] == 1
    assert payload["best"]["title"] == "微信"
