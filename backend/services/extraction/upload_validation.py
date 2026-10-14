"""Upload validation helpers for extraction endpoints."""

from __future__ import annotations

from fastapi import UploadFile

from backend.services.extraction.errors import (
    DocumentTooLarge,
    InvalidUpload,
    UnsupportedDocumentType,
)

PDF_SIGNATURE = b"%PDF-"
JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
READ_CHUNK_BYTES = 1024 * 1024


async def read_upload_bytes(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await upload.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DocumentTooLarge("Uploaded document exceeds the configured size limit.")
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content:
        raise InvalidUpload("Uploaded document is empty.")
    return content


def detect_supported_mime_type(content: bytes) -> str:
    if content.startswith(PDF_SIGNATURE):
        return "application/pdf"
    if content.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    if content.startswith(PNG_SIGNATURE):
        return "image/png"
    raise UnsupportedDocumentType("Uploaded document type is not supported.")
