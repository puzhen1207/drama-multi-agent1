"""Vue 工作台浏览器冒烟测试，不触发 LLM 生成。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--screenshot", default="outputs/vue-ui-smoke.png")
    parser.add_argument("--exercise-stub", action="store_true")
    parser.add_argument("--mock-stream", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    with sync_playwright() as playwright:
        edge_path = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        launch_options = {"headless": True}
        if edge_path.exists():
            launch_options["executable_path"] = str(edge_path)
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        if args.mock_stream:
            mock_events = [
                ("start", {"input": "帮我整理霸总追妻短剧大纲", "session_id": "mock-session"}),
                ("node_start", {"node": "parse_node"}),
                ("node_done", {"node": "parse_node", "duration_ms": 820, "summary": "类型=copywriting, 主题=霸总追妻"}),
                ("node_start", {"node": "retrieve_node"}),
                ("node_done", {"node": "retrieve_node", "duration_ms": 40, "summary": "召回 3 条素材", "references": [
                    {"material_id": "M_demo01", "title": "个人记忆 · 霸总追妻大纲", "category": "个人记忆", "score": 0.91, "source": "user_memory", "source_path": "用户个人素材 / ui-smoke", "owner_user_id": "ui-smoke"},
                    {"material_id": "P_demo02", "title": "霸总追妻人设参考", "category": "人设", "score": 0.82, "source": "public_knowledge", "source_path": "data/knowledge/examples/sample_scripts.json#item-1"},
                    {"material_id": "P_demo03", "title": "短剧爽文结构模板", "category": "方法", "score": 0.73, "source": "public_knowledge", "source_path": "data/knowledge/方法/production_playbooks.json#item-1"},
                ]}),
                ("node_start", {"node": "copywriting_node"}),
                ("node_done", {"node": "copywriting_node", "duration_ms": 1260, "summary": "生成内容 628 字"}),
                ("node_start", {"node": "audit_node"}),
                ("node_done", {"node": "audit_node", "duration_ms": 640, "summary": "passed=True, score=0.92, issues=1"}),
                ("workflow_done", {"elapsed_ms": 2760}),
                ("final", {"data": {"success": True, "content": "模拟生成正文，用于验证前端流程展示。", "task_type": "copywriting", "elapsed_ms": 2760, "iteration_count": 1, "session_id": "mock-session", "audit_result": {"passed": True, "score": 0.92, "issues": []}}}),
            ]
            mock_body = "".join(
                f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                for event_type, payload in mock_events
            )
            page.route(
                "**/v1/stream",
                lambda route: route.fulfill(status=200, content_type="text/event-stream", body=mock_body),
            )
            page.add_init_script("localStorage.setItem('drama_user_id', 'ui-smoke')")
        page.goto(args.base_url, wait_until="networkidle")

        assert page.title() == "短剧创作台"
        assert page.get_by_role("heading", name="短剧创作台").is_visible()
        assert page.get_by_text("写下你的需求", exact=True).is_visible()
        assert page.get_by_text("查看制作进度", exact=True).is_visible()
        assert page.get_by_text("查看生成动态", exact=True).is_visible()
        assert page.get_by_text("查看生成结果", exact=True).is_visible()
        panels = page.locator("main.studio-grid > section")
        assert panels.count() == 4, "主流程应包含四个独立阶段"
        boxes = [panels.nth(index).bounding_box() for index in range(4)]
        assert all(box is not None for box in boxes)
        assert all(boxes[index]["y"] < boxes[index + 1]["y"] for index in range(3)), "四个阶段应从上到下排列"
        assert max(box["width"] for box in boxes) - min(box["width"] for box in boxes) <= 1, "四个阶段应占据相同内容宽度"
        assert page.get_by_role("button", name="快速模式").get_attribute("class") == "mode-option--active"
        page.get_by_role("button", name="精细模式").click()
        assert "mode-option--active" in (page.get_by_role("button", name="精细模式").get_attribute("class") or "")
        page.get_by_role("button", name="快速模式").click()

        page.get_by_role("button", name="追妻大纲").click()
        assert "霸总追妻" in page.locator("textarea").first.input_value()
        assert page.get_by_role("button", name="开始生成").is_enabled()

        page.get_by_role("button", name="我的素材").click()
        assert page.get_by_role("dialog", name="我的素材").is_visible()
        page.get_by_role("button", name="关闭").click()

        page.get_by_role("button", name="设置").click()
        assert page.get_by_role("dialog", name="设置").is_visible()
        page.get_by_role("button", name="关闭").click()

        if args.mock_stream:
            page.get_by_role("button", name="开始生成").click()
            page.get_by_text("已识别为文案创作，主题：霸总追妻", exact=True).wait_for(timeout=5_000)
            assert page.get_by_text("已找到 3 条可用参考", exact=True).is_visible()
            assert page.get_by_text("参考来源明细", exact=True).is_visible()
            assert page.get_by_text("用户 ui-smoke", exact=True).is_visible()
            assert page.get_by_text("data/knowledge/examples/sample_scripts.json#item-1", exact=True).is_visible()
            assert page.get_by_text("已形成 628 字内容初稿", exact=True).is_visible()
            assert page.get_by_text("检查通过，得分 0.92，发现 1 项建议", exact=True).is_visible()
        elif args.exercise_stub:
            assert page.get_by_text("体验模式", exact=True).is_visible(), "仅允许在体验模式执行生成冒烟测试"
            page.get_by_role("button", name="开始生成").click()
            page.get_by_text("内容会显示在这里", exact=True).wait_for(state="hidden", timeout=20_000)
            assert page.locator(".script-copy").inner_text().strip()
            assert page.locator(".event-row").count() >= 2

        screenshot = Path(args.screenshot)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        mobile.goto(args.base_url, wait_until="networkidle")
        assert mobile.get_by_role("heading", name="短剧创作台").is_visible()
        overflow = mobile.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, f"移动端存在 {overflow}px 横向溢出"
        mobile_path = screenshot.with_name(f"{screenshot.stem}-mobile{screenshot.suffix}")
        mobile.screenshot(path=str(mobile_path), full_page=True)
        mobile.close()
        browser.close()

    if errors:
        raise AssertionError("浏览器控制台错误：\n" + "\n".join(errors))
    print(f"Vue UI smoke test passed; screenshot={screenshot}")


if __name__ == "__main__":
    main()
