"""Lớp embedding đa ngôn ngữ cho tìm kiếm vector (Giai đoạn 5).

sentence-transformers (kéo theo torch, ~1.3GB+) CHỈ được import bên trong hàm,
KHÔNG import ở đầu file — import module này (hay agent/rag/index.py) không được
phép kéo torch vào bộ nhớ nếu embedding chưa thật sự cần dùng. Cài qua
`pip install -r requirements-rag.txt`, không nằm trong requirements.txt gốc.

Model mặc định đa ngôn ngữ vì câu hỏi tiếng Việt cần tra cứu được tài liệu tiếng
Anh (man page, Ubuntu docs, Arch Wiki).
"""

from __future__ import annotations

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingProviderError(Exception):
    """Lỗi khi khởi tạo hoặc chạy embedding model."""


class EmbeddingProvider:
    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model = self._load_model(model_name)

    @staticmethod
    def _load_model(model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbeddingProviderError(
                "Chưa cài sentence-transformers. Chạy "
                "'pip install -r requirements-rag.txt' để bật tìm kiếm vector."
            ) from e

        try:
            return SentenceTransformer(model_name)
        except Exception as e:
            raise EmbeddingProviderError(f"Không tải được model embedding '{model_name}': {e}") from e

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        if hasattr(self._model, "get_embedding_dimension"):
            return self._model.get_embedding_dimension()
        return self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()
