import base64

from fastapi import HTTPException

from app.connectors.base import BaseConnector
from app.connectors.registry import register
from app.schemas.actions import TelegramFile, TelegramInject, TelegramSend


@register
class TelegramConnector(BaseConnector):
    key = "telegram"
    credential_fields = {"token"}
    required_credentials = {"token"}
    actions = {
        "send_message": TelegramSend,
        "get_file": TelegramFile,
        "inject_update": TelegramInject,
    }
    triggers = ["message", "document"]

    def api(self, method: str, params: dict) -> dict:
        token = self.ctx.credentials.get("token")
        if not token:
            raise HTTPException(
                422, {"detail": "Token Telegram absent", "code": "missing_credentials"}
            )
        response = self.ctx.http.post(
            f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise HTTPException(
                502,
                {"detail": "Telegram a refusé la requête", "code": "telegram_error"},
            )
        return data["result"]

    def test_connection(self) -> dict:
        if self.ctx.connection.is_mock:
            return {
                "ok": True,
                "mock": True,
                "bot": {"id": "demo-bot", "username": "demo_workflow_bot"},
            }
        bot = self.api("getMe", {})
        return {
            "ok": True,
            "mock": False,
            "bot": {"id": bot["id"], "username": bot.get("username")},
        }

    def download(self, file_id: str) -> dict:
        info = self.api("getFile", {"file_id": file_id})
        if info.get("file_size", 0) > self.ctx.settings.max_file_bytes:
            raise HTTPException(
                413,
                {
                    "detail": "Document Telegram trop volumineux",
                    "code": "file_too_large",
                },
            )
        path = info["file_path"]
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError("Invalid Telegram file path")
        url = f"https://api.telegram.org/file/bot{self.ctx.credentials['token']}/{path}"
        data = bytearray()
        with self.ctx.http.stream("GET", url, timeout=30) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > self.ctx.settings.max_file_bytes:
                    raise HTTPException(
                        413,
                        {
                            "detail": "Document Telegram trop volumineux",
                            "code": "file_too_large",
                        },
                    )
        return {
            "filename": path.rsplit("/", 1)[-1],
            "content_base64": base64.b64encode(data).decode(),
        }

    def execute(self, action: str, params: dict) -> dict:
        if action == "inject_update":
            if not self.ctx.connection.is_mock:
                raise HTTPException(
                    409,
                    {
                        "detail": "Injection réservée au mode démonstration",
                        "code": "mock_only",
                    },
                )
            if not params.get("text") and not params.get("document"):
                raise HTTPException(
                    422, {"detail": "Texte ou document requis", "code": "empty_event"}
                )
            if params.get("document"):
                try:
                    data = base64.b64decode(
                        params["document"]["content_base64"], validate=True
                    )
                except ValueError:
                    raise HTTPException(
                        422,
                        {
                            "detail": "Document base64 invalide",
                            "code": "invalid_document",
                        },
                    ) from None
                if len(data) > self.ctx.settings.max_file_bytes:
                    raise HTTPException(
                        413,
                        {
                            "detail": "Document trop volumineux",
                            "code": "file_too_large",
                        },
                    )
            artifact = self.record("telegram_update", params)
            return {"event_id": artifact.id, "mock": True}
        if action == "get_file":
            if self.ctx.connection.is_mock:
                raise HTTPException(
                    404,
                    {
                        "detail": (
                            "Utiliser inject_update avec un document en "
                            "mode démonstration"
                        ),
                        "code": "not_found",
                    },
                )
            return self.download(params["file_id"])
        if self.ctx.connection.is_mock:
            artifact = self.record("outbox", {"action": "send_message", **params})
            return {
                "message_id": artifact.id,
                "mock": True,
                "chat_id": params["chat_id"],
                "text": params["text"],
            }
        return {"mock": False, **self.api("sendMessage", params)}

    def poll(self, state: dict) -> tuple[list[dict], dict]:
        if self.ctx.connection.is_mock:
            seen = set(state.get("seen", []))
            events = [
                {
                    "event_id": record.id,
                    "type": "telegram.message",
                    "payload": record.data,
                }
                for record in self.records("telegram_update")
                if record.id not in seen
            ]
            return events, {
                "seen": list(
                    dict.fromkeys(
                        [
                            *state.get("seen", []),
                            *(event["event_id"] for event in events),
                        ]
                    )
                )[-1000:]
            }
        updates = self.api(
            "getUpdates",
            {
                "offset": state.get("offset", 0),
                "timeout": 20,
                "limit": 10,
                "allowed_updates": ["message"],
            },
        )
        events = []
        offset = state.get("offset", 0)
        for update in updates:
            offset = max(offset, update["update_id"] + 1)
            message = update.get("message", {})
            payload = {
                "chat_id": message.get("chat", {}).get("id"),
                "text": message.get("text", message.get("caption", "")),
            }
            if "document" in message:
                payload["document"] = self.download(message["document"]["file_id"])
                payload["document"]["filename"] = message["document"].get(
                    "file_name", payload["document"]["filename"]
                )
            if payload["text"] or "document" in payload:
                events.append(
                    {
                        "event_id": str(update["update_id"]),
                        "type": "telegram.message",
                        "payload": payload,
                    }
                )
        return events, {"offset": offset}
