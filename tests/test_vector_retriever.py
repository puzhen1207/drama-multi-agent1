"""公共素材向量索引的一致性测试。"""
from __future__ import annotations

import pytest

from drama_agent.tools.embedding import get_embedding_provider
from drama_agent.tools.vector_retriever import HierarchicalVectorStore


def test_load_repairs_embedding_dimension_mismatch(tmp_path):
    pytest.importorskip("faiss")
    index_dir = tmp_path / "repair_index"
    store = HierarchicalVectorStore(index_dir=index_dir)
    store.add_documents([
        {
            "title": "霸总追妻人设参考",
            "category": "人设",
            "content": "女主离开豪门后独立创业，男主发现误会并开始追妻，剧情需要多次反转。",
        },
        {
            "title": "古代侠客短剧结构",
            "category": "剧本",
            "content": "侠客夜闯城门营救故人，以三幕冲突和悬念结尾推动后续剧情。",
        },
    ])
    store.save()

    provider = get_embedding_provider()
    provider.dim = 32
    repaired = HierarchicalVectorStore(index_dir=index_dir)

    assert repaired.faiss_index.d == 32
    assert repaired.faiss_index.ntotal == repaired.count()["sub_blocks"]
    hits = repaired.search("霸总追妻短剧大纲", top_k_parent=1)
    assert hits and hits[0].title == "霸总追妻人设参考"
