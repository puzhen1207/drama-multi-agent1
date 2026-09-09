"""pytest 公共 fixture。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolated_stub_runtime(tmp_path, monkeypatch):
    """所有单元测试都离线、无真实凭据，并使用独立临时存储。"""
    from drama_agent import config
    import drama_agent.memory as memory_module
    import drama_agent.evaluation_store as evaluation_store_module
    import drama_agent.tools.embedding as embedding_module
    import drama_agent.tools.user_memory as user_memory_module
    import drama_agent.tools.vector_retriever as vector_module

    monkeypatch.setattr(config.settings, "llm_api_key", SecretStr(""))
    monkeypatch.setattr(config.settings, "api_user_tokens_json", SecretStr(""))
    monkeypatch.setattr(config.settings, "vector_index_path", str(tmp_path / "faiss_index"))
    monkeypatch.setattr(config.settings, "user_memory_path", str(tmp_path / "user_memory"))
    monkeypatch.setattr(config.settings, "evaluation_path", str(tmp_path / "evaluations"))

    def _use_hash_embedding(self):
        self.model = None
        self.mode = "hash_fallback"
        self.dim = 64

    monkeypatch.setattr(embedding_module.EmbeddingProvider, "_load_model", _use_hash_embedding)
    embedding_module._provider = None
    vector_module._vector_store = None
    user_memory_module._user_memory_store = None
    memory_module._session_manager = None
    evaluation_store_module._store = None
    yield
    embedding_module._provider = None
    vector_module._vector_store = None
    user_memory_module._user_memory_store = None
    memory_module._session_manager = None
    evaluation_store_module._store = None
