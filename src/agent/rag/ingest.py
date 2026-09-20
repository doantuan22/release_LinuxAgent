"""CLI thêm tài liệu vào user.db (Giai đoạn 5).

Chạy qua: PYTHONPATH=src python3 -m agent.rag.ingest --db user.db --title "..." \\
    --url "https://..." --summary "..." --distro-id ubuntu

summary phải do người dùng TỰ viết lại bằng lời riêng — tool này không tự tóm tắt
hộ hay lấy nguyên văn từ URL, để tránh vô tình chép lại nội dung có bản quyền
(GFDL/CC-BY-SA) vào DB.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from agent.rag.index import add_document, init_index_db
from agent.rag.schemas import DocumentRecord, RagConfig


class IngestValidationError(ValueError):
    """Input không hợp lệ — không bao giờ ghi vào DB khi lỗi này xảy ra."""


def _validate_summary(summary: str) -> None:
    if not summary or not summary.strip():
        raise IngestValidationError("summary không được rỗng.")


def _validate_source_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise IngestValidationError(
            f"source_url phải là URL http(s) hợp lệ, nhận được: '{source_url}'"
        )


def ingest_document(
    db_path: str | Path,
    *,
    title: str,
    summary: str,
    source_url: str,
    distro_id: str | None = None,
    license: str = "unknown",
    embedding_provider: object | None = None,
) -> DocumentRecord:
    _validate_summary(summary)
    _validate_source_url(source_url)

    config = RagConfig(embedding_enabled=embedding_provider is not None)
    # read_only=None (mặc định của init_index_db): KHÔNG ép DB đang có sẵn về writable —
    # nếu lỡ trỏ vào default.db đã đóng băng read_only=True, add_document() bên dưới
    # sẽ raise rõ ràng thay vì âm thầm mở khóa nó ra.
    init_index_db(db_path, config=config)

    content_hash = hashlib.sha256(f"{title}\n{summary}\n{source_url}".encode("utf-8")).hexdigest()
    record = DocumentRecord(
        id=str(uuid.uuid4()),
        title=title,
        summary=summary,
        source_url=source_url,
        license=license,
        distro_id=distro_id,
        content_hash=content_hash,
    )

    embedding = embedding_provider.embed(summary) if embedding_provider is not None else None
    add_document(db_path, record, embedding=embedding)

    return record


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-ingest", description="Thêm tài liệu vào user.db")
    parser.add_argument("--db", required=True, help="Đường dẫn tới user.db")
    parser.add_argument("--title", required=True)
    parser.add_argument("--url", required=True, dest="source_url")
    parser.add_argument(
        "--summary", required=True, help="Tóm tắt viết lại bằng lời riêng — KHÔNG copy nguyên văn nguồn"
    )
    parser.add_argument("--distro-id", default=None, help="Để trống = áp dụng cho mọi distro")
    parser.add_argument("--license", default="unknown")
    parser.add_argument("--embedding-enabled", action="store_true")
    parser.add_argument("--embedding-model", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    embedding_provider = None
    if args.embedding_enabled:
        from agent.rag.embeddings import DEFAULT_MODEL, EmbeddingProvider

        embedding_provider = EmbeddingProvider(args.embedding_model or DEFAULT_MODEL)

    try:
        record = ingest_document(
            args.db,
            title=args.title,
            summary=args.summary,
            source_url=args.source_url,
            distro_id=args.distro_id,
            license=args.license,
            embedding_provider=embedding_provider,
        )
    except IngestValidationError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1

    print(f"Đã thêm document '{record.title}' (id={record.id}) vào {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
