"""Dataclass mô tả tài liệu RAG và cấu hình embedding (Giai đoạn 5).

DocumentRecord CHỈ có "summary" (tóm tắt viết lại bằng lời riêng), KHÔNG có
trường "content" chứa nguyên văn — ràng buộc bản quyền bắt buộc: không đóng gói
nguyên văn man page/Ubuntu docs/Arch Wiki (GFDL/CC-BY-SA) vào bất kỳ DB nào,
chỉ lưu tóm tắt + link nguồn gốc.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1


@dataclass
class DocumentRecord:
    id: str
    title: str
    summary: str
    source_url: str
    license: str
    distro_id: str | None
    content_hash: str
    schema_version: int = SCHEMA_VERSION


@dataclass
class RagConfig:
    embedding_enabled: bool = False
    embedding_model: str | None = None
    rrf_k: int = 60
