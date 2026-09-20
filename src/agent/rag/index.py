"""Index tài liệu RAG: FTS5 (BM25) luôn bật, sqlite-vec (embedding) tùy chọn,
kết hợp bằng Reciprocal Rank Fusion (Giai đoạn 5).

FTS5 là nền tảng bắt buộc — không phụ thuộc extension ngoài. sqlite-vec là lớp
tùy chọn: nếu embedding_enabled nhưng môi trường không load được extension (thiếu
gói, thiếu quyền loadable-extension...), tự động tắt và fallback về FTS5 thuần,
LOG CẢNH BÁO rõ ràng — không bao giờ crash vì thiếu embedding (cùng triết lý với
fallback FTS5→LIKE ở agent/memory/user_memory.py, Giai đoạn 4).

sqlite-vec chỉ được import bên trong hàm (lazy) — module này phải import và dùng
được bình thường (ở chế độ FTS5 thuần) ngay cả khi chưa `pip install -r
requirements-rag.txt`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from agent.memory.schemas import check_schema_version
from agent.rag.schemas import DocumentRecord, RagConfig

logger = logging.getLogger(__name__)

# FTS5 có ngữ pháp truy vấn riêng (?, -, ", :, (, )... đều là ký tự cú pháp) — câu hỏi
# tự nhiên của người dùng ("...trên máy?") gây syntax error nếu đưa thẳng vào MATCH.
# Chỉ giữ lại token chữ/số (Unicode-aware) để không bao giờ đụng ký tự cú pháp FTS5.
_FTS5_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


# Từ chức năng tiếng Anh bị bỏ khỏi truy vấn trước khi nối OR, để câu hỏi tự nhiên
# ("how do I configure ... on my ...") không kéo theo match tràn lan qua các từ phổ
# biến. Nơi DUY NHẤT để mở rộng danh sách. Cố ý KHÔNG đưa vào từ trùng tên lệnh Linux
# (which, who, at, set, time, test...) vì đó có thể là nội dung tìm kiếm thật.
_FTS5_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "from", "by",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "these", "those",
    "how", "what", "why", "when", "where", "do", "does", "did", "can", "could",
    "should", "would", "will", "i", "me", "my", "we", "our", "you", "your",
    "please", "configure", "setup",
    # Tiếng Việt: chỉ từ chức năng thuần túy (giới từ/đại từ/từ nối/trợ từ). Cố ý KHÔNG
    # đưa vào âm tiết là một phần của thuật ngữ kỹ thuật (sao lưu/sao chép, thay thế,
    # từ khóa, khởi động lại, hệ thống, quản lý...) vì \w+ tách tiếng Việt theo âm tiết.
    "và", "của", "là", "cho", "để", "các", "những", "một", "này", "đó", "với", "khi",
    "trên", "trong", "theo", "đến", "ở", "về", "bằng", "như", "nếu", "thì", "mà",
    "có", "không", "được", "làm", "cách", "hãy", "tôi", "mình", "bạn", "nào", "gì",
    "đã", "đang", "sẽ",
})

# Cụm chức năng: từng âm tiết riêng lẻ là nội dung thật ("cấu trúc" = structure,
# "máy chủ" = server) nên KHÔNG thể đưa vào _FTS5_STOPWORDS; chỉ bỏ khi đi liền nhau,
# tương tự "configure" ở trên ("cấu hình" = configure).
_FTS5_STOP_PHRASES: tuple[str, ...] = ("cấu hình", "trên máy")
_FTS5_STOP_PHRASE_RE = re.compile(
    "|".join(r"(?<!\w)" + r"\s+".join(map(re.escape, p.split())) + r"(?!\w)" for p in _FTS5_STOP_PHRASES),
    re.IGNORECASE,
)


def _sanitize_fts5_query(query: str) -> str:
    query = _FTS5_STOP_PHRASE_RE.sub(" ", query)
    tokens = [t for t in _FTS5_TOKEN_RE.findall(query) if t.lower() not in _FTS5_STOPWORDS]
    # Nối bằng OR, không phải khoảng trắng: mặc định FTS5 coi nhiều token cách nhau
    # bằng khoảng trắng là AND (phải khớp HẾT) — quá chặt cho câu hỏi tự nhiên nhiều
    # từ, gần như luôn cho 0 kết quả. OR giữ được recall, bm25() vẫn xếp hạng đúng
    # theo mức độ khớp nhiều/ít từ.
    return " OR ".join(tokens)

_OVERSAMPLE_FACTOR = 3
_MIN_CANDIDATES = 20


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _is_read_only(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT value FROM _index_meta WHERE key = 'read_only'"
    ).fetchone()
    return row is not None and row[0] == "1"


def _vector_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'documents_vec'"
    ).fetchone()
    return row is not None


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Nạp extension sqlite-vec vào connection hiện tại. Bắt buộc gọi lại trên
    MỖI connection mới trước khi đụng tới bảng documents_vec — SQLite chỉ nhớ
    extension đã nạp trong phạm vi một connection, không phải trong file DB."""
    try:
        import sqlite_vec
    except ImportError:
        logger.warning(
            "embedding_enabled=True nhưng chưa cài sqlite-vec — chạy "
            "'pip install -r requirements-rag.txt' để bật tìm kiếm vector. "
            "Fallback về FTS5 (BM25) thuần."
        )
        return False

    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception as e:
        logger.warning(
            f"Không load được extension sqlite-vec ({e}) — môi trường này có thể "
            f"không hỗ trợ SQLite loadable extension. Fallback về FTS5 (BM25) "
            f"thuần, không bật tìm kiếm vector."
        )
        return False

    return True


def init_index_db(
    db_path: str | Path, *, read_only: bool | None = None, config: RagConfig | None = None
) -> bool:
    """Tạo bảng documents + FTS5 documents_fts nếu chưa có. Trả về True nếu
    tìm kiếm vector thực sự sẵn sàng (sqlite-vec load thành công), False nếu
    tắt hoặc fallback.

    read_only=None (mặc định) KHÔNG đụng tới cờ read_only đã có sẵn của DB —
    chỉ set về writable cho DB hoàn toàn mới. Truyền True/False tường minh để
    CHỦ ĐỘNG đổi trạng thái (vd seed_default_docs.py đóng băng default.db sau khi
    seed xong). Nhờ vậy gọi nhầm init_index_db qua ingest.py trên một DB đã bị
    đóng băng read_only=True sẽ KHÔNG âm thầm mở khóa nó ra.
    """
    config = config or RagConfig()

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_url TEXT NOT NULL,
                license TEXT NOT NULL,
                distro_id TEXT,
                content_hash TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
        check_schema_version(conn, "documents")

        if read_only is not None:
            conn.execute(
                "INSERT INTO _index_meta (key, value) VALUES ('read_only', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if read_only else "0",),
            )
        else:
            conn.execute("INSERT OR IGNORE INTO _index_meta (key, value) VALUES ('read_only', '0')")

        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING "
            "fts5(title, summary, doc_id UNINDEXED)"
        )
        conn.commit()

        if not config.embedding_enabled:
            return False

        return _try_load_sqlite_vec(conn)
    finally:
        conn.close()


def _row_to_document(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        id=row["id"],
        title=row["title"],
        summary=row["summary"],
        source_url=row["source_url"],
        license=row["license"],
        distro_id=row["distro_id"],
        content_hash=row["content_hash"],
        schema_version=row["schema_version"],
    )


def add_document(
    db_path: str | Path, record: DocumentRecord, *, embedding: list[float] | None = None
) -> None:
    conn = _connect(db_path)
    try:
        if _is_read_only(conn):
            raise PermissionError(
                f"'{db_path}' đang ở chế độ read_only — không được ghi thêm document "
                f"(default.db chỉ đọc sau khi seed xong, dùng user.db để thêm tài liệu mới)."
            )

        conn.execute(
            "INSERT INTO documents "
            "(id, title, summary, source_url, license, distro_id, content_hash, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.title,
                record.summary,
                record.source_url,
                record.license,
                record.distro_id,
                record.content_hash,
                record.schema_version,
            ),
        )
        conn.execute(
            "INSERT INTO documents_fts (title, summary, doc_id) VALUES (?, ?, ?)",
            (record.title, record.summary, record.id),
        )

        if embedding is not None:
            if not _try_load_sqlite_vec(conn):
                logger.warning(
                    f"Bỏ qua lưu embedding cho document '{record.id}' vì không load "
                    f"được sqlite-vec — document vẫn được lưu, chỉ thiếu tìm kiếm vector."
                )
            else:
                import sqlite_vec

                if not _vector_table_exists(conn):
                    dim = len(embedding)
                    conn.execute(
                        f"CREATE VIRTUAL TABLE documents_vec USING "
                        f"vec0(embedding float[{dim}], doc_id text)"
                    )
                conn.execute(
                    "INSERT INTO documents_vec(embedding, doc_id) VALUES (?, ?)",
                    (sqlite_vec.serialize_float32(embedding), record.id),
                )

        conn.commit()
    finally:
        conn.close()


def search_bm25(
    db_path: str | Path, query: str, *, distro_id: str | None = None, limit: int = 10
) -> list[DocumentRecord]:
    """FTS5 MATCH trên (title, summary). distro_id khớp được BOOST lên trước,
    KHÔNG loại bỏ cứng document của distro khác — nhiều tài liệu dùng chung mọi
    distro (vd cách đọc journalctl)."""
    sanitized_query = _sanitize_fts5_query(query)
    if not sanitized_query:
        return []

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                """
                SELECT d.id, d.title, d.summary, d.source_url, d.license,
                       d.distro_id, d.content_hash, d.schema_version
                FROM documents_fts f
                JOIN documents d ON d.id = f.doc_id
                WHERE documents_fts MATCH ?
                ORDER BY
                    CASE
                        WHEN ? IS NOT NULL AND d.distro_id = ? THEN 0
                        WHEN d.distro_id IS NULL THEN 1
                        ELSE 2
                    END,
                    bm25(documents_fts) ASC
                LIMIT ?
                """,
                (sanitized_query, distro_id, distro_id, limit),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Truy vấn FTS5 không hợp lệ ('{query}'): {e} — trả về không có kết quả.")
            return []
    finally:
        conn.close()

    return [_row_to_document(row) for row in rows]


def search_vector(db_path: str | Path, query_embedding: list[float], *, limit: int = 10) -> list[str]:
    """Trả về danh sách doc_id, sắp theo khoảng cách tăng dần (gần nhất trước).
    Trả [] nếu chưa có document nào có embedding hoặc sqlite-vec không load được."""
    conn = _connect(db_path)
    try:
        if not _vector_table_exists(conn):
            return []
        if not _try_load_sqlite_vec(conn):
            return []

        import sqlite_vec

        rows = conn.execute(
            "SELECT doc_id FROM documents_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_embedding), limit),
        ).fetchall()
    finally:
        conn.close()

    return [row[0] for row in rows]


def _fetch_documents_by_ids(db_path: str | Path, ids: list[str]) -> dict[str, DocumentRecord]:
    if not ids:
        return {}

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, title, summary, source_url, license, distro_id, content_hash, schema_version "
            f"FROM documents WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        conn.close()

    return {row["id"]: _row_to_document(row) for row in rows}


def search(
    db_path: str | Path,
    query: str,
    *,
    distro_id: str | None = None,
    limit: int = 10,
    config: RagConfig | None = None,
    embedding_provider: object | None = None,
) -> list[tuple[DocumentRecord, float]]:
    """BM25 (luôn chạy) + vector (chỉ khi config.embedding_enabled và có
    embedding_provider), kết hợp bằng Reciprocal Rank Fusion:
    score(doc) = sum trên mỗi ranker mà doc xuất hiện của 1/(rrf_k + rank).
    Không tìm thấy gì -> trả về [] , không bịa document."""
    config = config or RagConfig()
    candidate_limit = max(limit * _OVERSAMPLE_FACTOR, _MIN_CANDIDATES)

    bm25_ranked = search_bm25(db_path, query, distro_id=distro_id, limit=candidate_limit)
    doc_lookup: dict[str, DocumentRecord] = {doc.id: doc for doc in bm25_ranked}

    rrf_scores: dict[str, float] = {}
    for rank, doc in enumerate(bm25_ranked, start=1):
        rrf_scores[doc.id] = rrf_scores.get(doc.id, 0.0) + 1.0 / (config.rrf_k + rank)

    if config.embedding_enabled and embedding_provider is not None:
        query_embedding = embedding_provider.embed(query)
        vector_doc_ids = search_vector(db_path, query_embedding, limit=candidate_limit)

        missing_ids = [doc_id for doc_id in vector_doc_ids if doc_id not in doc_lookup]
        if missing_ids:
            doc_lookup.update(_fetch_documents_by_ids(db_path, missing_ids))

        for rank, doc_id in enumerate(vector_doc_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (config.rrf_k + rank)

    ranked_ids = sorted(rrf_scores, key=lambda doc_id: rrf_scores[doc_id], reverse=True)[:limit]
    return [(doc_lookup[doc_id], rrf_scores[doc_id]) for doc_id in ranked_ids if doc_id in doc_lookup]
