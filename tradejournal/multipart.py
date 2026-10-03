"""multipart/form-data, the way browsers send file uploads, read with the
standard library's email parser (Python 3.13 removed the cgi module)."""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP


@dataclass
class Upload:
    field: str
    filename: str
    data: bytes


def parse(content_type: str, body: bytes) -> tuple[dict[str, str], list[Upload]]:
    """(the plain fields, the files). ValueError if it isn't multipart form data."""
    if (
        not content_type.lower().startswith("multipart/form-data")
        or "boundary=" not in content_type
    ):
        raise ValueError("not multipart form data")
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
        "latin-1"
    )
    msg = BytesParser(policy=HTTP).parsebytes(head + body)
    if not msg.is_multipart():
        raise ValueError("not multipart form data")
    fields: dict[str, str] = {}
    files: list[Upload] = []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is None:
            fields[name] = data.decode("utf-8", "replace")
        elif data:
            files.append(Upload(name, filename, data))
    return fields, files
