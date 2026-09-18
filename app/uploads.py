from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile


MEDIA_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class UploadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    original_name: str
    relative_path: str
    media_type: str
    size: int
    sha256: str
    absolute_path: Path


def safe_original_name(filename: str | None) -> str:
    raw = (filename or "resume").replace("\\", "/").rsplit("/", 1)[-1]
    raw = unicodedata.normalize("NFKC", raw)
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()
    if not raw:
        raw = "resume"
    return raw[:180]


def _validate_signature(path: Path, extension: str) -> None:
    with path.open("rb") as stream:
        header = stream.read(16)
    if extension == "pdf" and not header.startswith(b"%PDF-"):
        raise UploadValidationError("The selected file is not a valid PDF document.")
    if extension == "doc" and not header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise UploadValidationError("The selected file is not a valid Word .doc document.")
    if extension == "docx":
        if not header.startswith(b"PK"):
            raise UploadValidationError("The selected file is not a valid Word .docx document.")
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 5_000 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                    raise UploadValidationError("The Word document expands beyond the safe limit.")
                names = {item.filename for item in entries}
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise UploadValidationError(
                        "The selected file is not a valid Word .docx document."
                    )
        except zipfile.BadZipFile as exc:
            raise UploadValidationError(
                "The selected file is not a valid Word .docx document."
            ) from exc


async def store_resume(
    upload: UploadFile,
    *,
    application_id: str,
    resume_root: Path,
    allowed_extensions: tuple[str, ...],
    max_bytes: int,
) -> StoredUpload:
    original_name = safe_original_name(upload.filename)
    extension = Path(original_name).suffix.lower().lstrip(".")
    if extension not in allowed_extensions:
        accepted = ", ".join(f".{item}" for item in allowed_extensions)
        raise UploadValidationError(f"Please upload one of these file types: {accepted}.")

    now = datetime.now(timezone.utc)
    destination_dir = resume_root / f"{now.year:04d}" / f"{now.month:02d}" / application_id
    destination_dir.mkdir(parents=True, exist_ok=False)
    final_path = destination_dir / f"resume.{extension}"
    temp_path = destination_dir / ".uploading"
    digest = hashlib.sha256()
    size = 0

    try:
        with temp_path.open("xb") as stream:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError(
                        f"The resume is larger than the {max_bytes // (1024 * 1024)} MB limit."
                    )
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())

        if size == 0:
            raise UploadValidationError("The uploaded resume is empty.")
        _validate_signature(temp_path, extension)
        os.replace(temp_path, final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        try:
            destination_dir.rmdir()
        except OSError:
            pass
        raise
    finally:
        await upload.close()

    relative_path = final_path.relative_to(resume_root).as_posix()
    return StoredUpload(
        original_name=original_name,
        relative_path=relative_path,
        media_type=MEDIA_TYPES[extension],
        size=size,
        sha256=digest.hexdigest(),
        absolute_path=final_path,
    )


def resolve_stored_resume(resume_root: Path, relative_path: str) -> Path:
    root = resume_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FileNotFoundError("Stored resume path is outside the upload directory") from exc
    if not candidate.is_file():
        raise FileNotFoundError("Stored resume no longer exists")
    return candidate
