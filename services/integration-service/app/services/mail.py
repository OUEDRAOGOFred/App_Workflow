import base64
from email.message import EmailMessage

from fastapi import HTTPException


def message(params: dict, sender: str | None = None) -> EmailMessage:
    mail = EmailMessage()
    mail["To"] = params["to"]
    mail["Subject"] = params["subject"]
    if sender:
        mail["From"] = sender
    mail.set_content(params["body"])
    if params.get("html"):
        mail.add_alternative(params["body"], subtype="html")
    attachment = params.get("attachment")
    if attachment:
        try:
            data = base64.b64decode(attachment["content_base64"], validate=True)
        except ValueError:
            raise HTTPException(
                422,
                {
                    "detail": "Pièce jointe base64 invalide",
                    "code": "invalid_attachment",
                },
            ) from None
        if len(data) > 5242880:
            raise HTTPException(
                413,
                {"detail": "Pièce jointe trop volumineuse", "code": "file_too_large"},
            )
        main, sub = attachment["content_type"].split("/", 1)
        mail.add_attachment(
            data, maintype=main, subtype=sub, filename=attachment["filename"]
        )
    return mail
