import base64
import copy
import re

from fastapi import HTTPException

from app.connectors.base import BaseConnector


def seed(connector: BaseConnector) -> None:
    connector.record(
        "sheet",
        {
            "id": "demo-sheet",
            "name": "Démonstration Workflow",
            "sheets": {"Documents": []},
        },
    )
    connector.record(
        "gmail_message",
        {
            "id": "demo-mail",
            "subject": "Facture de démonstration",
            "from": "supplier@example.com",
            "text": "Facture numéro 42. Total 125 euros TTC.",
            "labelIds": ["INBOX", "UNREAD"],
            "attachments": [
                {
                    "id": "demo-attachment",
                    "filename": "facture.txt",
                    "content_base64": base64.b64encode(
                        b"Facture numero 42, montant 125 euros"
                    ).decode(),
                }
            ],
        },
    )


def columns(value: str) -> int:
    result = 0
    for char in value:
        result = result * 26 + ord(char) - 64
    return result - 1


def a1(value: str) -> tuple[str, int, int, int | None, int | None]:
    title, sep, cell = value.rpartition("!")
    if not sep:
        title = "Documents"
        cell = value
    title = title.strip("'")
    match = re.fullmatch(
        r"([A-Za-z]+)([1-9][0-9]*)?(?::([A-Za-z]+)([1-9][0-9]*)?)?", cell
    )
    if not match:
        raise HTTPException(
            422,
            {
                "detail": "Plage A1 invalide en mode démonstration",
                "code": "invalid_range",
            },
        )
    first, row, last, end = match.groups()
    start_row = int(row or 1) - 1
    start_col = columns(first.upper())
    end_row = int(end) if end else (int(row) if row and not last else None)
    end_col = columns(last.upper()) + 1 if last else start_col + 1
    if (
        start_row > 9999
        or start_col > 999
        or (end_row and end_row > 10000)
        or end_col > 1000
        or (end_row and end_row <= start_row)
        or end_col <= start_col
    ):
        raise HTTPException(
            422, {"detail": "Plage hors limites", "code": "invalid_range"}
        )
    return title, start_row, start_col, end_row, end_col


def execute(connector: BaseConnector, action: str, params: dict) -> dict:
    if action == "gmail_labels":
        return {
            "labels": [
                {"id": "INBOX", "name": "INBOX"},
                {"id": "UNREAD", "name": "UNREAD"},
            ],
            "mock": True,
        }
    if action == "gmail_read":
        messages = [item.data for item in connector.records("gmail_message")]
        query = params.get("query", "")
        for term in query.split():
            if term == "is:unread":
                messages = [item for item in messages if "UNREAD" in item["labelIds"]]
            elif term.startswith("from:"):
                messages = [item for item in messages if term[5:] in item["from"]]
            else:
                messages = [
                    item
                    for item in messages
                    if term.lower() in (item["subject"] + " " + item["text"]).lower()
                ]
        if params.get("from_email"):
            messages = [
                item for item in messages if params["from_email"] in item["from"]
            ]
        if params.get("unread"):
            messages = [item for item in messages if "UNREAD" in item["labelIds"]]
        if params.get("label_ids"):
            messages = [
                item
                for item in messages
                if set(params["label_ids"]).issubset(item["labelIds"])
            ]
        return {"messages": messages[: params["max_results"]], "mock": True}
    if action == "gmail_attachment":
        for record in connector.records("gmail_message"):
            if record.data["id"] == params["message_id"]:
                for attachment in record.data.get("attachments", []):
                    if attachment["id"] == params["attachment_id"]:
                        return {**attachment, "mock": True}
        raise HTTPException(
            404, {"detail": "Pièce jointe introuvable", "code": "not_found"}
        )
    if action == "gmail_send":
        from app.services.mail import message

        message(params)
        record = connector.record("outbox", {"action": action, **params})
        return {"id": record.id, "mock": True}
    sheets = connector.records("sheet")
    if action == "sheets_list":
        return {
            "spreadsheets": [
                {"id": record.data["id"], "name": record.data["name"]}
                for record in sheets
            ],
            "mock": True,
        }
    record = next(
        (record for record in sheets if record.data["id"] == params["spreadsheet_id"]),
        None,
    )
    if record is None:
        raise HTTPException(
            404, {"detail": "Classeur introuvable", "code": "not_found"}
        )
    data = copy.deepcopy(record.data)
    if action == "sheets_tabs":
        return {
            "sheets": [
                {"title": title, "sheetId": index}
                for index, title in enumerate(data["sheets"])
            ],
            "mock": True,
        }
    if action == "sheets_create":
        if params["title"] in data["sheets"]:
            raise HTTPException(
                409, {"detail": "Feuille existante", "code": "sheet_exists"}
            )
        data["sheets"][params["title"]] = []
        record.data = data
        return {"title": params["title"], "mock": True}
    title, start, col, end, last = a1(params["range"])
    if title not in data["sheets"]:
        raise HTTPException(404, {"detail": "Feuille introuvable", "code": "not_found"})
    rows = data["sheets"][title]
    if action == "sheets_read":
        return {
            "range": params["range"],
            "values": [row[col:last] for row in rows[start:end]],
            "mock": True,
        }
    values = params["values"]
    if any(len(row) > 100 for row in values):
        raise HTTPException(
            422,
            {
                "detail": "100 cellules maximum par ligne",
                "code": "invalid_action_params",
            },
        )
    if action == "sheets_append":
        start = len(rows)
    if (
        start + len(values) > 10000
        or len(rows) * max((len(row) for row in rows), default=1) > 100000
    ):
        raise HTTPException(
            413,
            {"detail": "Classeur de démonstration plein", "code": "mock_sheet_full"},
        )
    for index, value in enumerate(values, start):
        while len(rows) <= index:
            rows.append([])
        row = rows[index]
        while len(row) < col + len(value):
            row.append("")
        row[col : col + len(value)] = value
    record.data = data
    return {"updatedRows": len(values), "range": params["range"], "mock": True}
