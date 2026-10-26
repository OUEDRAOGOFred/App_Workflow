import ipaddress
import json
import socket
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException

from app.connectors.base import BaseConnector
from app.connectors.registry import register
from app.schemas.actions import HttpRequest


@register
class HTTPConnector(BaseConnector):
    key = "http"
    credential_fields = {"base_url", "authorization"}
    always_required_credentials = {"base_url"}
    actions = {"request": HttpRequest}

    def test_connection(self) -> dict:
        if self.ctx.connection.is_mock:
            return {"ok": True, "mock": True}
        result = self.execute("request", {"method": "GET", "path": ""})
        return {
            "ok": 200 <= result["status_code"] < 400,
            "mock": False,
            "status_code": result["status_code"],
        }

    def execute(self, action: str, params: dict) -> dict:
        base = self.ctx.credentials.get("base_url", "")
        url = params.get("url") or urljoin(base, params.get("path", ""))
        parsed = urlsplit(url)
        origin = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise HTTPException(
                422,
                {
                    "detail": "URL HTTP(S) sans identifiants requise",
                    "code": "invalid_url",
                },
            )
        if base and (parsed.scheme, parsed.hostname, parsed.port) != (
            origin.scheme,
            origin.hostname,
            origin.port,
        ):
            raise HTTPException(
                422,
                {
                    "detail": "L’URL doit conserver l’origine de la connexion",
                    "code": "origin_mismatch",
                },
            )
        if self.ctx.connection.is_mock:
            data = {
                "action": "request",
                "url": url,
                "method": params.get("method", "GET"),
                "body": params.get("body"),
            }
            artifact = self.record("outbox", data) if self.ctx.connection.id else None
            return {
                "status_code": 200,
                "data": {"request_id": artifact.id if artifact else None, "mock": True},
                "mock": True,
            }
        allowed = {
            host.strip()
            for host in self.ctx.settings.http_allowed_hosts.split(",")
            if host.strip()
        }
        try:
            addresses = list(
                dict.fromkeys(
                    info[4][0]
                    for info in socket.getaddrinfo(
                        parsed.hostname,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                        type=socket.SOCK_STREAM,
                    )
                )
            )
        except OSError:
            raise HTTPException(
                502, {"detail": "Hôte HTTP inaccessible", "code": "http_unavailable"}
            ) from None
        if not addresses or (
            parsed.hostname not in allowed
            and any(
                not ipaddress.ip_address(address).is_global
                or ipaddress.ip_address(address).is_multicast
                for address in addresses
            )
        ):
            raise HTTPException(
                422,
                {
                    "detail": (
                        "Hôte privé interdit ; configurer "
                        "HTTP_ALLOWED_HOSTS pour une exception explicite"
                    ),
                    "code": "blocked_host",
                },
            )
        # Pin the validated address. Keep Host and TLS SNI for the original host.
        ip = addresses[0]
        network_host = f"[{ip}]" if ":" in ip else ip
        target = parsed._replace(
            netloc=network_host + (f":{parsed.port}" if parsed.port else "")
        ).geturl()
        headers = {"Host": parsed.netloc, "Cookie": ""}
        if self.ctx.credentials.get("authorization"):
            headers["Authorization"] = self.ctx.credentials["authorization"]
        with self.ctx.http.stream(
            params.get("method", "GET"),
            target,
            json=params.get("body"),
            headers=headers,
            extensions={"sni_hostname": parsed.hostname},
            timeout=15,
            follow_redirects=False,
        ) as response:
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 1048576:
                    raise HTTPException(
                        413,
                        {
                            "detail": "Réponse HTTP trop volumineuse",
                            "code": "response_too_large",
                        },
                    )
            try:
                result = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                result = bytes(data).decode("utf-8", errors="replace")
            return {"status_code": response.status_code, "data": result, "mock": False}
