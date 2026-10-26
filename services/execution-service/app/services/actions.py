import base64
import operator

import httpx

from app.core.config import get_settings
from app.services.auth import access_token
from app.services.expressions import ensure_size, lookup, resolve


class NodeError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class Actions:
    def __init__(self, client: httpx.AsyncClient, user: str):
        self.client = client
        self.user = user
        self.settings = get_settings()

    async def request(
        self,
        service: str,
        method: str,
        path: str,
        body=None,
        files=None,
        internal=False,
    ):
        url = {
            "agent": self.settings.agent_service_url,
            "workflow": self.settings.workflow_service_url,
            "integration": self.settings.integration_service_url,
            "classifier": self.settings.classifier_service_url,
        }[service] + path
        try:
            response = await self.client.request(
                method,
                url,
                json=body if files is None else None,
                files=files,
                headers={
                    "Authorization": "Bearer "
                    + access_token(self.user, service=internal),
                    "Cookie": "",
                },
            )
        except httpx.HTTPError:
            raise NodeError(
                "upstream_unavailable", "Le service ne répond pas", True
            ) from None
        if response.status_code >= 400:
            try:
                code = response.json().get("code", "upstream_error")
            except ValueError:
                code = "upstream_error"
            if (
                not isinstance(code, str)
                or not code.replace("_", "").isalnum()
                or len(code) > 80
            ):
                code = "upstream_error"
            message = (
                "Connexion expirée, reconnecter"
                if code == "connection_expired"
                else "L’agent ou le connecteur a refusé l’action"
            )
            raise NodeError(
                code,
                message,
                response.status_code >= 500 or response.status_code == 429,
            )
        if response.status_code == 204:
            return {}
        try:
            result = response.json()
        except ValueError:
            raise NodeError(
                "invalid_response", "Réponse JSON du service invalide"
            ) from None
        if not isinstance(result, dict):
            raise NodeError("invalid_response", "Une réponse objet est requise")
        return result

    async def run(
        self,
        agent: dict,
        data: dict,
        context: dict,
        trigger_type: str,
        is_root: bool = False,
    ) -> dict:
        kind = agent["type_key"]
        config = resolve(agent.get("config", {}), context)
        ensure_size(config)
        if kind == "transform":
            return config["mapping"]
        if kind == "condition":
            field = agent["config"]["field"]
            if "{{" in field:
                left = resolve(field, context)
            else:
                left = lookup(
                    context,
                    (
                        field
                        if field.startswith(("input.", "trigger.", "nodes."))
                        else "input." + field
                    ),
                )
            right = config["value"]
            operations = {
                "eq": operator.eq,
                "ne": operator.ne,
                "gt": operator.gt,
                "gte": operator.ge,
                "lt": operator.lt,
                "lte": operator.le,
                "contains": lambda a, b: b in a,
            }
            try:
                value = bool(operations[config["operator"]](left, right))
            except (TypeError, KeyError):
                raise NodeError(
                    "invalid_condition", "Valeurs incompatibles pour la condition"
                ) from None
            return {"result": value, "branch": "true" if value else "false"}
        if kind == "telegram_trigger":
            if data.get("document") and not config.get("accept_documents", True):
                raise NodeError(
                    "documents_disabled", "Cet agent n’accepte pas les documents"
                )
            return data
        if kind == "gmail_read" and is_root and trigger_type == "gmail":
            return {**data, "messages": [data]}
        if kind == "document_classifier":
            if config.get("classes"):
                available = await self.request("classifier", "GET", "/classes")
                if set(config["classes"]) != set(available["classes"]):
                    raise NodeError(
                        "classes_mismatch",
                        "Configurer CLASSES sur le service de classification",
                    )
            text = data.get("text")
            document = data.get("document") or (data.get("attachments") or [None])[0]
            if (
                document
                and not document.get("content_base64")
                and document.get("id")
                and context.get("source_connection_id")
            ):
                message_id = (
                    data.get("message_id")
                    or data.get("id")
                    or context["trigger"].get("id")
                )
                if message_id:
                    downloaded = await self.request(
                        "integration",
                        "POST",
                        f"/connections/{context['source_connection_id']}/actions/gmail_attachment",
                        {
                            "params": {
                                "message_id": message_id,
                                "attachment_id": document["id"],
                            }
                        },
                    )
                    document = {**document, **downloaded}
            if document and document.get("content_base64"):
                try:
                    content = base64.b64decode(
                        document["content_base64"], validate=True
                    )
                except (ValueError, TypeError):
                    raise NodeError(
                        "invalid_document", "Document base64 invalide"
                    ) from None
                if len(content) > 5242880:
                    raise NodeError("file_too_large", "Document trop volumineux")
                result = await self.request(
                    "classifier",
                    "POST",
                    "/classify/file",
                    files={"file": (document.get("filename", "document.txt"), content)},
                )
                if result.get("confidence", 0) < config.get(
                    "confidence_threshold", 0.5
                ):
                    result["label"] = "inconnu"
                    result["reason"] = "below_threshold"
                return result
            if not isinstance(text, str) or not text.strip():
                raise NodeError(
                    "missing_text", "Texte ou document requis pour classifier"
                )
            return await self.request(
                "classifier",
                "POST",
                "/classify",
                {
                    "text": text,
                    "confidence_threshold": config.get("confidence_threshold", 0.5),
                },
            )
        if kind == "llm":
            return await self.request(
                "classifier",
                "POST",
                "/llm",
                {
                    "prompt": config["prompt"],
                    "temperature": config.get("temperature", 0.2),
                },
            )
        connection = agent.get("connection_id")
        if not connection and kind == "http_request":
            return await self.request(
                "integration", "POST", "/integrations/http/request", config
            )
        if not connection:
            raise NodeError("connection_required", "Connexion requise pour cet agent")
        action = {
            "telegram_send": "send_message",
            "gmail_read": "gmail_read",
            "gmail_send": "gmail_send",
            "sheets_append_row": "sheets_append",
            "sheets_read": "sheets_read",
            "http_request": "request",
        }.get(kind)
        if action is None:
            raise NodeError("unsupported_agent_type", "Type d’agent non exécutable")
        params = dict(config)
        if kind == "sheets_append_row":
            params["values"] = [config["values"]]
        if kind == "gmail_read":
            params.setdefault("max_results", 10)
        result = await self.request(
            "integration",
            "POST",
            f"/connections/{connection}/actions/{action}",
            {"params": params},
        )
        if kind == "http_request" and result.get("status_code", 200) >= 400:
            status = result["status_code"]
            raise NodeError(
                "http_action_failed",
                f"Le serveur HTTP a répondu {status}",
                status >= 500 or status == 429,
            )
        return result
