from pydantic import BaseModel, ConfigDict, EmailStr, Field, JsonValue


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Empty(Params):
    pass


class Attachment(Params):
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n/\\]+$")
    content_base64: str = Field(max_length=7000000)
    content_type: str = Field(
        default="application/octet-stream", pattern=r"^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$"
    )


class SendMail(Params):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=500, pattern=r"^[^\r\n]+$")
    body: str = Field(max_length=200000)
    html: bool = False
    attachment: Attachment | None = None


class ReadMail(Params):
    query: str = Field(default="", max_length=1000)
    from_email: EmailStr | None = None
    unread: bool = False
    label_ids: list[str] = Field(default_factory=list, max_length=20)
    max_results: int = Field(default=10, ge=1, le=100)


class GmailAttachment(Params):
    message_id: str = Field(min_length=1, max_length=300)
    attachment_id: str = Field(min_length=1, max_length=300)


class GmailLabel(Params):
    message_id: str = Field(min_length=1, max_length=300)
    label_id: str = Field(min_length=1, max_length=300)


class Spreadsheet(Params):
    spreadsheet_id: str = Field(min_length=1, max_length=300)


class ReadRange(Spreadsheet):
    range: str = Field(min_length=1, max_length=300)


class WriteRange(ReadRange):
    values: list[list[JsonValue]] = Field(min_length=1, max_length=200)


class AddSheet(Spreadsheet):
    title: str = Field(min_length=1, max_length=100)


class TelegramSend(Params):
    chat_id: str | int
    text: str = Field(min_length=1, max_length=4096)


class TelegramFile(Params):
    file_id: str = Field(min_length=1, max_length=300)


class TelegramInject(Params):
    text: str = Field(default="", max_length=100000)
    chat_id: str | int = "demo-chat"
    document: Attachment | None = None


class HttpRequest(Params):
    url: str | None = Field(default=None, max_length=2048)
    path: str = Field(default="", max_length=2048)
    method: str = Field(default="GET", pattern=r"^(GET|POST|PUT|DELETE)$")
    body: JsonValue = None
