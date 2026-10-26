import base64
import email
import imaplib
import smtplib
import ssl

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter

from app.connectors.base import BaseConnector
from app.connectors.registry import register
from app.schemas.actions import ReadMail, SendMail
from app.services.mail import message


@register
class SMTPConnector(BaseConnector):
    key = "gmail_smtp"
    credential_fields = {"email", "app_password"}
    required_credentials = {"email", "app_password"}
    actions = {"gmail_send": SendMail, "gmail_read": ReadMail}
    triggers = ["gmail.new_mail"]

    def credentials(self) -> tuple[str, str]:
        address = str(
            TypeAdapter(EmailStr).validate_python(self.ctx.credentials.get("email", ""))
        )
        password = self.ctx.credentials.get("app_password", "")
        if not password:
            raise HTTPException(
                422,
                {
                    "detail": "Mot de passe d’application Gmail requis",
                    "code": "missing_credentials",
                },
            )
        return address, password

    def test_connection(self) -> dict:
        if self.ctx.connection.is_mock:
            return {"ok": True, "mock": True}
        address, password = self.credentials()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(address, password)
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=15) as imap:
            imap.login(address, password)
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise ValueError("IMAP mailbox unavailable")
        return {"ok": True, "mock": False}

    def execute(self, action: str, params: dict) -> dict:
        if action == "gmail_send":
            mail = message(params, self.ctx.credentials.get("email"))
            if self.ctx.connection.is_mock:
                record = self.record("outbox", {"action": action, **params})
                return {"id": record.id, "mock": True}
            address, password = self.credentials()
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(address, password)
                smtp.send_message(mail, from_addr=address, to_addrs=[params["to"]])
            return {"sent": True, "mock": False}
        if params.get("query") or params.get("label_ids"):
            raise HTTPException(
                422,
                {
                    "detail": (
                        "SMTP/IMAP accepte from_email et unread ; les "
                        "requêtes q et libellés nécessitent OAuth Gmail"
                    ),
                    "code": "unsupported_imap_filter",
                },
            )
        if self.ctx.connection.is_mock:
            messages = [record.data for record in self.records("gmail_message")]
            if params.get("from_email"):
                messages = [
                    item
                    for item in messages
                    if params["from_email"].lower() in item.get("from", "").lower()
                ]
            if params.get("unread"):
                messages = [item for item in messages if item.get("unread", True)]
            return {"messages": messages[: params["max_results"]], "mock": True}
        address, password = self.credentials()
        criteria = ["UNSEEN"] if params.get("unread") else ["ALL"]
        if params.get("from_email"):
            criteria.extend(["FROM", f'"{params["from_email"]}"'])
        messages = []
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=15) as imap:
            imap.login(address, password)
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise ValueError("Mailbox unavailable")
            status, data = imap.uid("search", None, *criteria)
            if status != "OK":
                raise ValueError("Search failed")
            for uid in data[0].split()[-params["max_results"] :][::-1]:
                status, parts = imap.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise ValueError("Fetch failed")
                raw = next(part[1] for part in parts if isinstance(part, tuple))
                if len(raw) > self.ctx.settings.max_file_bytes:
                    continue
                parsed = email.message_from_bytes(raw)
                attachments = []
                texts = []
                for part in parsed.walk():
                    content = part.get_payload(decode=True)
                    if content is None:
                        continue
                    if part.get_filename():
                        attachments.append(
                            {
                                "filename": str(part.get_filename()),
                                "content_base64": base64.b64encode(content).decode(),
                                "content_type": part.get_content_type(),
                            }
                        )
                    elif part.get_content_type() == "text/plain":
                        texts.append(
                            content.decode(
                                part.get_content_charset() or "utf-8", errors="replace"
                            )
                        )
                messages.append(
                    {
                        "id": uid.decode(),
                        "subject": str(parsed.get("Subject", "")),
                        "from": str(parsed.get("From", "")),
                        "text": "\n".join(texts),
                        "attachments": attachments,
                    }
                )
        return {"messages": messages, "mock": False}

    def poll(self, state: dict) -> tuple[list[dict], dict]:
        result = self.run("gmail_read", {"unread": True, "max_results": 20})
        seen = set(state.get("seen", []))
        events = [
            {"event_id": item["id"], "type": "gmail.new_mail", "payload": item}
            for item in result["messages"]
            if item["id"] not in seen
        ]
        return events, {
            "seen": list(
                dict.fromkeys(
                    [*state.get("seen", []), *(event["event_id"] for event in events)]
                )
            )[-1000:]
        }
