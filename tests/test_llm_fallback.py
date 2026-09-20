"""降级提示不得混淆缺少配置和已配置但调用失败。"""
from drama_agent import llm
from drama_agent.exceptions import LLMTimeoutError


def test_configured_call_failure_has_accurate_hint(monkeypatch):
    monkeypatch.setattr(llm, "llm_available", lambda: True)

    def fail(*args, **kwargs):
        raise LLMTimeoutError("LLM 网络错误: ConnectError")

    monkeypatch.setattr(llm, "_call_http_api", fail)
    output = llm.chat("【任务类型】script_generation\n【期望字数】1000", "内部系统提示")
    assert "已配置 API Key，但本次模型调用失败" in output
    assert "未配置有效 API Key" not in output
    assert "【任务类型】" not in output
    assert "内部系统提示" not in output


def test_missing_key_has_configuration_hint(monkeypatch):
    monkeypatch.setattr(llm, "llm_available", lambda: False)
    output = llm.chat("写第一章")
    assert "未配置有效 API Key" in output
    assert "不是模型生成的正式剧本" in output
