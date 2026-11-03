from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .schema import App

SENSITIVE = re.compile(
    r"authorization|cookie|password|secret|(?:^|_)token(?:$|_)|api.?key|device.?id|session.?id|activation.?code",
    re.I,
)
BLOCK_ACTION = re.compile(
    r"删除|付款|支付|购买|发送通知|发布|部署|提交订单|delete|purchase|pay\b|deploy|publish", re.I
)


def scrub(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(k) else scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value, flags=re.I)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|token|secret|password|authorization)=)[^&\s]+", r"\1[REDACTED]", value
        )
        return value
    return value


def safe_url(url: str) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    if p.port:
        host += f":{p.port}"
    return urlunsplit(
        (
            p.scheme,
            host,
            p.path,
            urlencode([(k, "[REDACTED]" if SENSITIVE.search(k) else v) for k, v in parse_qsl(p.query)]),
            "",
        )
    )


class Policy:
    def __init__(self, app: App):
        self.app = app
        self.room: str | None = None

    def check_url(self, url: str) -> str:
        target = urljoin(self.app.base_url, url)
        p = urlsplit(target)
        if p.scheme not in {"http", "https", "ws", "wss"} or p.username or p.password:
            raise ValueError("URL scheme or credentials forbidden")
        if (p.hostname or "").lower() not in {h.lower() for h in self.app.allowed_hosts}:
            raise ValueError("host is outside configured allowlist")
        if self.app.environment == "local" and p.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("local environment requires loopback URL")
        room = dict(parse_qsl(p.query)).get("room")
        if self.room and room and room != self.room:
            raise ValueError("cannot switch to another user's room")
        return target

    def check_request(self, url: str, navigation: bool, method: str = "GET"):
        try:
            return self.check_url(url)
        except ValueError:
            p = urlsplit(url)
            if (
                not navigation
                and p.scheme in {"http", "https"}
                and not p.username
                and not p.password
                and (p.hostname or "").lower() in {h.lower() for h in self.app.resource_hosts}
            ):
                return url
            if (
                not navigation
                and self.app.resource_policy == "public_readonly"
                and method in {"GET", "HEAD"}
                and p.scheme in {"http", "https"}
                and not p.username
                and not p.password
            ):
                host = (p.hostname or "").lower()
                if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                    raise ValueError("private resource host blocked")
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    return url
                if address.is_global:
                    return url
            raise

    def check_control(self, node: dict):
        if BLOCK_ACTION.search(node.get("name", "")):
            raise ValueError("action is outside UX evaluation scope")
        if self.app.adapter == "duckking" and (
            node.get("dom_id") == "create-room" or node.get("name", "").startswith("房间 ")
        ):
            raise ValueError("keep the room allocated for this run")
        if node.get("href"):
            self.check_url(node["href"])
