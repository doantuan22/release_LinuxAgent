"""Câu trả lời ngoại tuyến khi không gọi được LLM nào (Giai đoạn 6).

format_offline_answer() KHÔNG bao giờ gọi LLM — chỉ format lại kết quả RAG cục bộ
(đã có sẵn không cần mạng, vì FTS5/BM25 chạy hoàn toàn local) thành text thuần, có
nhãn rõ đây là chế độ ngoại tuyến để người dùng không nhầm là câu trả lời từ model.
"""

from __future__ import annotations

from agent.rag.schemas import DocumentRecord

_OFFLINE_LABEL = "[CHẾ ĐỘ NGOẠI TUYẾN — không qua LLM]"


def format_offline_answer(user_message: str, rag_results: list[DocumentRecord]) -> str:
    if not rag_results:
        return (
            f"{_OFFLINE_LABEL}\n"
            f"Không thể kết nối tới LLM lúc này, và không tìm thấy tài liệu nội bộ nào "
            f"liên quan tới câu hỏi: \"{user_message}\". Vui lòng thử lại khi có mạng, "
            f"hoặc thử diễn đạt câu hỏi bằng từ khóa khác."
        )

    lines = [
        _OFFLINE_LABEL,
        f"Không thể kết nối tới LLM lúc này. Dưới đây là các tài liệu nội bộ liên quan "
        f"tới \"{user_message}\" tìm được cục bộ (không qua model, có thể chưa đúng hoàn "
        f"toàn với ý bạn hỏi):",
        "",
    ]
    for doc in rag_results:
        lines.append(f"- {doc.title}")
        lines.append(f"  {doc.summary}")
        lines.append(f"  Nguồn: {doc.source_url}")
        lines.append("")

    return "\n".join(lines).rstrip()
