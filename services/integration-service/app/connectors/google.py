import base64

import httplib2
from fastapi import HTTPException
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.connectors.base import BaseConnector
from app.connectors.registry import register
from app.schemas.actions import (
    AddSheet,
    Empty,
    GmailAttachment,
    GmailLabel,
    ReadMail,
    ReadRange,
    SendMail,
    Spreadsheet,
    WriteRange,
)
from app.services import mock_google
from app.services.mail import message


@register
class GoogleConnector(BaseConnector):
    key = "google"
    actions = {
        "gmail_labels": Empty,
        "gmail_read": ReadMail,
        "gmail_send": SendMail,
        "gmail_attachment": GmailAttachment,
        "gmail_add_label": GmailLabel,
        "sheets_list": Empty,
        "sheets_tabs": Spreadsheet,
        "sheets_read": ReadRange,
        "sheets_append": WriteRange,
        "sheets_update": WriteRange,
        "sheets_create": AddSheet,
    }
    triggers = ["gmail.new_mail"]

    def service(self, name: str, version: str):
        return build(
            name,
            version,
            http=AuthorizedHttp(
                self.ctx.google_credentials, http=httplib2.Http(timeout=15)
            ),
            cache_discovery=False,
        )

    def test_connection(self) -> dict:
        if not self.ctx.connection.is_mock:
            self.service("gmail", "v1").users().getProfile(userId="me").execute()
            self.service("drive", "v3").files().list(
                pageSize=1, fields="files(id)"
            ).execute()
        return {"ok": True, "mock": self.ctx.connection.is_mock}

    def execute(self, action: str, params: dict) -> dict:
        if action == "gmail_add_label":
            raise HTTPException(
                403,
                {
                    "detail": (
                        "L’ajout de libellés exige gmail.modify, absent "
                        "des quatre scopes autorisés"
                    ),
                    "code": "insufficient_scope",
                    "help_url": "/api/v1/integrations/google/checklist",
                },
            )
        if self.ctx.connection.is_mock:
            return mock_google.execute(self, action, params)
        if action.startswith("gmail_"):
            gmail = self.service("gmail", "v1").users()
            if action == "gmail_labels":
                return gmail.labels().list(userId="me").execute()
            if action == "gmail_send":
                raw = base64.urlsafe_b64encode(message(params).as_bytes()).decode()
                return {
                    "mock": False,
                    **gmail.messages().send(userId="me", body={"raw": raw}).execute(),
                }
            if action == "gmail_attachment":
                result = (
                    gmail.messages()
                    .attachments()
                    .get(
                        userId="me",
                        messageId=params["message_id"],
                        id=params["attachment_id"],
                    )
                    .execute()
                )
                raw = result.get("data", "")
                content = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
                if len(content) > self.ctx.settings.max_file_bytes:
                    raise HTTPException(
                        413,
                        {
                            "detail": "Pièce jointe trop volumineuse",
                            "code": "file_too_large",
                        },
                    )
                return {
                    "content_base64": base64.b64encode(content).decode(),
                    "size": len(content),
                    "mock": False,
                }
            query = params.get("query", "")
            if params.get("from_email"):
                query += " from:" + params["from_email"]
            if params.get("unread"):
                query += " is:unread"
            result = (
                gmail.messages()
                .list(
                    userId="me",
                    q=query.strip(),
                    labelIds=params.get("label_ids", []),
                    maxResults=params["max_results"],
                )
                .execute()
            )
            messages = []
            for item in result.get("messages", []):
                content = (
                    gmail.messages()
                    .get(userId="me", id=item["id"], format="full")
                    .execute()
                )
                headers = {
                    header["name"].lower(): header["value"]
                    for header in content.get("payload", {}).get("headers", [])
                }
                texts = []
                attachments = []

                def parts(part, texts=texts, attachments=attachments):
                    body = part.get("body", {})
                    if part.get("filename") and body.get("attachmentId"):
                        attachments.append(
                            {
                                "id": body["attachmentId"],
                                "filename": part["filename"],
                                "content_type": part.get("mimeType"),
                            }
                        )
                    elif part.get("mimeType") == "text/plain" and body.get("data"):
                        raw = body["data"]
                        texts.append(
                            base64.urlsafe_b64decode(
                                raw + "=" * (-len(raw) % 4)
                            ).decode("utf-8", errors="replace")
                        )
                    for child in part.get("parts", []):
                        parts(child)

                parts(content.get("payload", {}))
                messages.append(
                    {
                        "id": content["id"],
                        "subject": headers.get("subject", ""),
                        "from": headers.get("from", ""),
                        "text": "\n".join(texts)[:200000],
                        "snippet": content.get("snippet", ""),
                        "labelIds": content.get("labelIds", []),
                        "attachments": attachments,
                    }
                )
            return {"messages": messages, "mock": False}
        if action == "sheets_list":
            result = (
                self.service("drive", "v3")
                .files()
                .list(
                    q=(
                        "mimeType='application/vnd.google-apps.spreadshee"
                        "t' and trashed=false"
                    ),
                    fields="files(id,name),nextPageToken",
                    pageSize=100,
                )
                .execute()
            )
            return {
                "spreadsheets": result.get("files", []),
                "next_page_token": result.get("nextPageToken"),
                "mock": False,
            }
        sheets = self.service("sheets", "v4").spreadsheets()
        sid = params["spreadsheet_id"]
        if action == "sheets_tabs":
            result = sheets.get(
                spreadsheetId=sid, fields="sheets(properties)"
            ).execute()
            return {
                "sheets": [item["properties"] for item in result.get("sheets", [])],
                "mock": False,
            }
        if action == "sheets_create":
            return sheets.batchUpdate(
                spreadsheetId=sid,
                body={
                    "requests": [
                        {"addSheet": {"properties": {"title": params["title"]}}}
                    ]
                },
            ).execute()
        if action == "sheets_read":
            return (
                sheets.values().get(spreadsheetId=sid, range=params["range"]).execute()
            )
        if action == "sheets_append":
            return (
                sheets.values()
                .append(
                    spreadsheetId=sid,
                    range=params["range"],
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": params["values"]},
                )
                .execute()
            )
        return (
            sheets.values()
            .update(
                spreadsheetId=sid,
                range=params["range"],
                valueInputOption="RAW",
                body={"values": params["values"]},
            )
            .execute()
        )

    def poll(self, state: dict) -> tuple[list[dict], dict]:
        config = self.ctx.connection.poll_config
        result = self.run(
            "gmail_read",
            {
                "query": config.get("query", "is:unread"),
                "label_ids": config.get("label_ids", []),
                "max_results": 20,
            },
        )
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
