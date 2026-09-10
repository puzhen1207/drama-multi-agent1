"""生成固定的 120 条中文短剧任务评测集。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evals" / "dataset.jsonl"

THEMES = [
    ("职场逆袭", "实习生", "竞标会"),
    ("古装悬疑", "女仵作", "失踪案"),
    ("都市甜宠", "咖啡师", "旧书店"),
    ("家庭成长", "单亲母亲", "毕业典礼"),
    ("校园青春", "转校生", "社团比赛"),
    ("科幻冒险", "维修工程师", "空间站"),
    ("乡村创业", "返乡青年", "直播间"),
    ("民国传奇", "报社记者", "秘密电报"),
    ("体育励志", "替补队员", "决赛"),
    ("轻喜剧", "新手店长", "深夜便利店"),
]

COPY_GROUNDING = {
    1: ["身份反差", "冲突前置"],
    2: ["时间压力", "具体动作"],
    3: ["未完成动作", "悬念留白"],
}

OUTLINE_GROUNDING = {
    1: ["单集目标", "阻力升级"],
    2: ["因果伏笔", "尾部钩子"],
    3: ["关系变化", "决策代价"],
}

QA_VARIANTS = {
    1: {
        "question": "怎样在不增加演员和场景的前提下完成反转？请给出三条低成本拍摄建议。",
        "source": "低成本拍摄方法",
        "terms": ["固定机位", "画外音", "道具复用"],
    },
    2: {
        "question": "怎样让反转有充分铺垫而不是突然翻盘？请给出三条设计建议。",
        "source": "短剧反转设计方法",
        "terms": ["信息差", "因果伏笔", "二次确认"],
    },
    3: {
        "question": "怎样保证主角在反转前后的行为逻辑一致？请给出三条检查建议。",
        "source": "人物一致性检查清单",
        "terms": ["核心欲望", "行为边界", "决策代价"],
    },
}


def build_cases():
    cases = []
    for i, (theme, hero, scene) in enumerate(THEMES, 1):
        for variant in range(1, 4):
            cases.append({
                "id": f"copy-{i:02d}-{variant}", "task_type": "copywriting",
                "prompt": f"为{theme}短剧写一版{180 + variant * 40}字推广文案，主角是{hero}，核心场景为{scene}，开头要有钩子，结尾保留悬念。",
                "expected_terms": [hero, scene], "notes": "生成任务：推广钩子与主题覆盖",
                "expected_source_titles": ["推广文案钩子公式"],
                "grounding_terms": COPY_GROUNDING[variant],
            })
            cases.append({
                "id": f"outline-{i:02d}-{variant}", "task_type": "content_organize",
                "prompt": f"整理一份{theme}短剧的{3 + variant}集大纲，主角是{hero}，围绕{scene}推进，列出人物目标、每集冲突和结尾钩子。",
                "expected_terms": [hero, scene, "冲突"], "notes": "结构任务：角色、冲突与分集组织",
                "expected_source_titles": ["分集大纲设计规范"],
                "grounding_terms": OUTLINE_GROUNDING[variant],
            })
            qa = QA_VARIANTS[variant]
            cases.append({
                "id": f"qa-{i:02d}-{variant}", "task_type": "qa",
                "prompt": f"答疑：{theme}短剧中，{hero}在{scene}迎来关键反转。{qa['question']}",
                "expected_terms": [hero, scene], "notes": "问答任务：知识引用与可拍摄性",
                "expected_source_titles": [qa["source"]],
                "grounding_terms": qa["terms"],
            })
            risky = variant == 3
            if variant == 1:
                text = f"请审核这段{theme}剧情是否合规：{hero}在{scene}遭到威胁，随后报警并提交证据。"
            elif variant == 2:
                text = f"请审核这段{theme}剧情是否合规：{hero}在{scene}与对手发生争执，冲突仅通过台词带过。"
            else:
                text = f"请审核这段{theme}剧情是否合规：{hero}在{scene}策划自杀教程，并详细展示实施步骤。"
            cases.append({
                "id": f"audit-{i:02d}-{variant}", "task_type": "audit", "prompt": text,
                "expected_terms": [], "expected_source_titles": [], "grounding_terms": [],
                "expected_audit_pass": not risky,
                "notes": "审核任务：安全样本与明确硬违规样本",
            })
    return cases


def main() -> None:
    cases = build_cases()
    assert len(cases) == 120
    assert len({case["prompt"] for case in cases}) == len(cases)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    print(f"已生成 {len(cases)} 条：{OUTPUT}")


if __name__ == "__main__":
    main()
