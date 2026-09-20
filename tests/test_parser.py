"""任务解析 Agent 单元测试（不依赖 LLM）。"""
from __future__ import annotations

from drama_agent.agents.parser_agent import _rule_based_parse, run_parse
from drama_agent.models import ParsedTask


def test_rule_based_parse_copywriting():
    task = _rule_based_parse("写2版推广文案，推广都市新剧《错位人生》")
    assert task.task_type == "copywriting"
    assert task.needs_retrieval is True


def test_rule_based_parse_script_chapter():
    task = _rule_based_parse("写一个主角为博兴是傻子的短剧内容第一章")
    assert task.task_type == "script_generation"
    assert task.target_length == 1000


def test_marketing_word_keeps_copywriting_even_with_short_drama():
    task = _rule_based_parse("为这个短剧内容写一版投放推广文案")
    assert task.task_type == "copywriting"


def test_rule_based_parse_audit():
    task = _rule_based_parse("帮我检查这段剧本是否有违规内容")
    assert task.task_type == "audit"
    assert task.needs_retrieval is False


def test_rule_based_parse_qa():
    task = _rule_based_parse("短剧创作中哪些内容是红线？")
    assert task.task_type == "qa"


def test_rule_based_parse_target_length():
    task = _rule_based_parse("写一段300字的短剧开头")
    assert task.target_length == 300


def test_run_parse_stub_mode():
    state = {"raw_input": "给我整理一个关于霸总追妻的短剧大纲，分3集"}
    result = run_parse(state)
    assert "parsed_task" in result
    assert result["parsed_task"].task_type == "content_organize"


def test_fast_mode_skips_llm_parser(monkeypatch):
    import drama_agent.agents.parser_agent as parser_module

    monkeypatch.setattr(parser_module, "llm_available", lambda: True)

    def unexpected_llm_call(**_):
        raise AssertionError("快速模式不应调用 LLM 解析")

    monkeypatch.setattr(parser_module, "chat_structured", unexpected_llm_call)
    result = parser_module.run_parse({
        "raw_input": "写一段都市短剧推广文案，300字",
        "run_mode": "fast",
    })
    assert result["parsed_task"].task_type == "copywriting"
    assert "快速模式" in result["parsed_task"].raw_explanation


def test_quality_mode_corrects_llm_script_misclassification(monkeypatch):
    import drama_agent.agents.parser_agent as parser_module

    monkeypatch.setattr(parser_module, "llm_available", lambda: True)
    monkeypatch.setattr(parser_module, "chat_structured", lambda **_: ParsedTask(
        task_type="copywriting",
        topic="博兴短剧第一章",
        style="爽文",
        target_length=500,
        requirements="写第一章",
        raw_explanation="模型误判为推广文案",
    ))
    result = parser_module.run_parse({
        "raw_input": "写一个主角为博兴是傻子的短剧内容第一章",
        "run_mode": "quality",
    })
    assert result["parsed_task"].task_type == "script_generation"
    assert result["parsed_task"].target_length >= 800
