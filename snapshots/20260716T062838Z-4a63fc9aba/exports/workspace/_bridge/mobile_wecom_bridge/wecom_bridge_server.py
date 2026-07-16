#!/usr/bin/env python3
"""Enterprise WeChat (WeCom) mobile bridge server.

The server is deliberately small and conservative:
- stdlib HTTP server, no always-on GUI dependency;
- SQLite queue for messages;
- dry-run endpoint for local validation without WeCom secrets;
- real WeCom callbacks fail closed when AES support is unavailable.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from mobile_queue import MobileQueue


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.local.json"
DEFAULT_DB = ROOT / "mobile_bridge.db"
LOG_DIR = ROOT / "logs"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def env_or_value(config: dict[str, Any], key: str, default: str = "") -> str:
    env_name = config.get(f"{key}_env")
    if env_name:
        return os.environ.get(env_name, default)
    return str(config.get(key, default) or default)


def json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def parse_qs(path: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlparse(path)
    query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    return parsed.path, query


def sha1_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    parts = sorted([token, timestamp, nonce, encrypted])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify_signature(token: str, signature: str, timestamp: str, nonce: str, encrypted: str) -> bool:
    expected = sha1_signature(token, timestamp, nonce, encrypted)
    return hmac.compare_digest(expected, signature or "")


def extract_encrypt_from_xml(xml_body: bytes) -> str:
    root = ElementTree.fromstring(xml_body)
    encrypt = root.findtext("Encrypt")
    if not encrypt:
        raise ValueError("missing Encrypt field in WeCom XML body")
    return encrypt


class WeComCrypto:
    """Minimal wrapper around optional AES-CBC support.

    WeCom callback decryption needs AES. The Python stdlib does not provide AES,
    so real callback decryption requires either `cryptography` or another
    reviewed implementation. Dry-run mode does not need this class.
    """

    def __init__(self, encoding_aes_key: str, corp_id: str) -> None:
        self.encoding_aes_key = encoding_aes_key
        self.corp_id = corp_id
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
        except Exception as exc:  # pragma: no cover - depends on local optional package
            raise RuntimeError(
                "Real WeCom callback decryption requires package 'cryptography'. "
                "Install it only after approving environment changes."
            ) from exc
        self._Cipher = Cipher
        self._algorithms = algorithms
        self._modes = modes
        self._backend = default_backend()

    def _key(self) -> bytes:
        key_text = self.encoding_aes_key
        if len(key_text) != 43:
            raise ValueError("EncodingAESKey must be 43 characters")
        return base64.b64decode(key_text + "=")

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        pad = data[-1]
        if pad < 1 or pad > 32:
            raise ValueError("invalid PKCS#7 padding")
        return data[:-pad]

    def decrypt(self, encrypted: str) -> str:
        key = self._key()
        cipher = self._Cipher(
            self._algorithms.AES(key),
            self._modes.CBC(key[:16]),
            backend=self._backend,
        )
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
        plaintext = self._unpad(plaintext)
        msg_len = int.from_bytes(plaintext[16:20], "big")
        msg = plaintext[20 : 20 + msg_len]
        corp_id = plaintext[20 + msg_len :].decode("utf-8")
        if self.corp_id and corp_id != self.corp_id:
            raise ValueError("corp_id mismatch while decrypting WeCom callback")
        return msg.decode("utf-8")


def parse_wecom_text(xml_text: str) -> dict[str, str]:
    root = ElementTree.fromstring(xml_text.encode("utf-8"))
    return {
        "to_user": root.findtext("ToUserName") or "",
        "from_user": root.findtext("FromUserName") or "",
        "create_time": root.findtext("CreateTime") or "",
        "msg_type": root.findtext("MsgType") or "",
        "content": root.findtext("Content") or "",
        "msg_id": root.findtext("MsgId") or "",
        "agent_id": root.findtext("AgentID") or "",
    }


class WeComSender:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def _corp_id(self) -> str:
        return env_or_value(self.config["wecom"], "corp_id")

    def _corp_secret(self) -> str:
        return env_or_value(self.config["wecom"], "corp_secret")

    def _agent_id(self) -> int:
        return int(self.config["wecom"].get("agent_id", 0))

    def _request_json(self, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            method = "POST"
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_access_token(self) -> str:
        corp_id = self._corp_id()
        secret = self._corp_secret()
        if not corp_id or not secret:
            raise ValueError("corp_id/corp_secret are not configured")
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken?"
            + urllib.parse.urlencode({"corpid": corp_id, "corpsecret": secret})
        )
        payload = self._request_json(url)
        if payload.get("errcode") != 0:
            raise RuntimeError(f"WeCom gettoken failed: {payload}")
        return payload["access_token"]

    def send_text(self, touser: str, content: str) -> dict[str, Any]:
        token = self.get_access_token()
        url = "https://qyapi.weixin.qq.com/cgi-bin/message/send?" + urllib.parse.urlencode(
            {"access_token": token}
        )
        payload = {
            "touser": touser,
            "msgtype": "text",
            "agentid": self._agent_id(),
            "text": {"content": content[:2048]},
            "safe": 0,
            "enable_id_trans": 0,
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 1800,
        }
        return self._request_json(url, payload)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "WeComMobileBridge/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    @property
    def app(self) -> "BridgeServer":
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        path, query = parse_qs(self.path)
        if path == "/health":
            health = self.app.queue.health()
            health.update({"service": "wecom-mobile-bridge", "time": int(time.time())})
            json_response(self, health)
            return
        if path == "/tasks":
            limit = int(query.get("limit", "10"))
            json_response(self, {"ok": True, "tasks": self.app.queue.list_tasks(limit)})
            return
        if path == "/wecom/callback":
            self.handle_wecom_verify(query)
            return
        json_response(self, {"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path, query = parse_qs(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        if path == "/dry-run/enqueue":
            self.handle_dry_run(raw)
            return
        if path == "/wecom/callback":
            self.handle_wecom_message(query, raw)
            return
        if path == "/send":
            self.handle_send(raw)
            return
        json_response(self, {"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def handle_dry_run(self, raw: bytes) -> None:
        payload = json.loads(raw.decode("utf-8") or "{}")
        result = self.app.queue.enqueue(
            payload.get("text", ""),
            source=payload.get("source", "dry-run"),
            external_user=payload.get("user", "local-test"),
            external_conversation=payload.get("conversation", "dry-run"),
            metadata={"dry_run": True},
        )
        json_response(self, {"ok": True, "task": result})

    def handle_wecom_verify(self, query: dict[str, str]) -> None:
        token = env_or_value(self.app.config["wecom"], "token")
        corp_id = env_or_value(self.app.config["wecom"], "corp_id")
        aes_key = env_or_value(self.app.config["wecom"], "encoding_aes_key")
        encrypted = query.get("echostr", "")
        if not verify_signature(
            token,
            query.get("msg_signature", ""),
            query.get("timestamp", ""),
            query.get("nonce", ""),
            encrypted,
        ):
            text_response(self, "signature mismatch", HTTPStatus.FORBIDDEN)
            return
        try:
            plain = WeComCrypto(aes_key, corp_id).decrypt(encrypted)
        except Exception as exc:
            logging.exception("WeCom verify failed")
            text_response(self, f"verify failed: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        text_response(self, plain)

    def handle_wecom_message(self, query: dict[str, str], raw: bytes) -> None:
        token = env_or_value(self.app.config["wecom"], "token")
        corp_id = env_or_value(self.app.config["wecom"], "corp_id")
        aes_key = env_or_value(self.app.config["wecom"], "encoding_aes_key")
        try:
            encrypted = extract_encrypt_from_xml(raw)
            if not verify_signature(
                token,
                query.get("msg_signature", ""),
                query.get("timestamp", ""),
                query.get("nonce", ""),
                encrypted,
            ):
                text_response(self, "signature mismatch", HTTPStatus.FORBIDDEN)
                return
            plain_xml = WeComCrypto(aes_key, corp_id).decrypt(encrypted)
            msg = parse_wecom_text(plain_xml)
            if msg["msg_type"] != "text":
                self.app.queue.add_event("wecom", "ignored_non_text", msg)
                text_response(self, "success")
                return
            task = self.app.queue.enqueue(
                msg["content"],
                source="wecom",
                external_user=msg["from_user"],
                external_conversation=msg["agent_id"],
                metadata={"msg_id": msg["msg_id"], "create_time": msg["create_time"]},
            )
            self.app.queue.add_event("wecom", "callback_received", {"task": task}, task["id"])
            text_response(self, "success")
        except Exception as exc:
            logging.exception("WeCom callback failed")
            json_response(self, {"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_send(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
            touser = payload["touser"]
            content = payload["content"]
            result = WeComSender(self.app.config).send_text(touser, content)
            json_response(self, {"ok": True, "wecom": result})
        except (KeyError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            json_response(self, {"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)


class BridgeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], config: dict[str, Any], queue: MobileQueue) -> None:
        super().__init__(address, BridgeHandler)
        self.config = config
        self.queue = queue


def configure_logging(config: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = Path(config.get("logging", {}).get("file", LOG_DIR / "wecom_bridge.log"))
    if not log_file.is_absolute():
        log_file = ROOT / log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, str(config.get("logging", {}).get("level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stderr)],
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WeCom mobile bridge server")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.local.json")
    parser.add_argument("--host", help="Override listen host")
    parser.add_argument("--port", type=int, help="Override listen port")
    parser.add_argument("--init-db", action="store_true", help="Initialize database and exit")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    config = load_config(Path(args.config))
    configure_logging(config)

    db_path = Path(config.get("queue", {}).get("db_path", DEFAULT_DB))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    queue = MobileQueue(db_path, config=config)
    if args.init_db:
        print(f"initialized {db_path}")
        return 0

    host = args.host or config.get("server", {}).get("host", "127.0.0.1")
    port = args.port or int(config.get("server", {}).get("port", 8787))
    server = BridgeServer((host, port), config, queue)
    logging.info("WeCom mobile bridge listening on %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("WeCom mobile bridge stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
