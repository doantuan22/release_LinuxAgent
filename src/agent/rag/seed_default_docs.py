"""Seed default.db với bộ tài liệu mặc định (Giai đoạn 5).

Chỉ chứa tóm tắt viết lại bằng lời riêng + link nguồn gốc — KHÔNG chép nguyên văn
nội dung có bản quyền GFDL/CC-BY-SA nào (xem data/default_docs_seed.json). Sau khi
seed xong, DB được đánh dấu read_only=True vĩnh viễn — default.db đóng gói sẵn, chỉ
đọc; người dùng muốn thêm tài liệu riêng thì dùng user.db qua `agent.rag.ingest`.

Chạy: PYTHONPATH=src python3 -m agent.rag.seed_default_docs --db data/default.db

default.db được đóng gói sẵn; lệnh này dành cho lúc tái tạo artifact phát hành.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from agent.core.resource_loader import get_resource
from agent.rag.index import add_document, init_index_db
from agent.rag.schemas import DocumentRecord, RagConfig


def _content_hash(title: str, summary: str, source_url: str) -> str:
    return hashlib.sha256(f"{title}\n{summary}\n{source_url}".encode("utf-8")).hexdigest()


def seed_default_docs(db_path: str | Path, seed_file: Path | None = None) -> int:
    if seed_file is None:
        with get_resource("default_docs_seed.json") as source:
            entries = json.loads(source.read_text(encoding="utf-8"))
    else:
        entries = json.loads(seed_file.read_text(encoding="utf-8"))

    init_index_db(db_path, read_only=False, config=RagConfig(embedding_enabled=False))

    count = 0
    for entry in entries:
        record = DocumentRecord(
            id=entry["id"],
            title=entry["title"],
            summary=entry["summary"],
            source_url=entry["source_url"],
            license=entry["license"],
            distro_id=entry.get("distro_id"),
            content_hash=_content_hash(entry["title"], entry["summary"], entry["source_url"]),
        )
        add_document(db_path, record)
        count += 1

    # Đóng băng default.db thành read_only NGAY SAU khi seed xong.
    init_index_db(db_path, read_only=True, config=RagConfig(embedding_enabled=False))

    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed default.db với tài liệu Linux mặc định")
    parser.add_argument("--db", required=True)
    args = parser.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    count = seed_default_docs(args.db)
    print(f"Đã seed {count} document vào '{args.db}', DB giờ ở chế độ read_only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
