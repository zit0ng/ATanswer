"""AI 客户端 / 答案解析的单元测试（使用本地模拟服务，不访问外网）。"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ai_client  # noqa: E402
from tools.mock_ai_server import start_mock_ai_server, stop_mock_ai_server  # noqa: E402


class AIClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server, self.base_url = start_mock_ai_server(answer="B")
        self.addCleanup(lambda: stop_mock_ai_server(self.server))
        self.config = ai_client.AIConfig(
            base_url=self.base_url,
            api_key="sk-test-123",
            model="mock-vl",
            timeout_s=10,
        )

    def test_ask_question_ok(self) -> None:
        answer = ai_client.ask_question(b"\x89PNG-fake-image", self.config)
        self.assertEqual(answer.letter, "B")
        self.assertIn("答案", answer.raw)
        self.assertGreaterEqual(answer.elapsed_ms, 0)
        self.assertEqual(len(self.server.requests), 1)

    def test_request_contains_image_and_model(self) -> None:
        ai_client.ask_question(b"fake-png", self.config)
        request = self.server.requests[0]
        self.assertTrue(request["path"].endswith("/chat/completions"))
        self.assertEqual(request["headers"].get("Authorization"), "Bearer sk-test-123")
        payload = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(payload["model"], "mock-vl")
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_validate_config(self) -> None:
        self.assertTrue(ai_client.validate_config(self.config)[0])
        bad = ai_client.AIConfig(base_url="ftp://x", api_key="k", model="m")
        self.assertFalse(ai_client.validate_config(bad)[0])
        self.assertFalse(
            ai_client.validate_config(ai_client.AIConfig(base_url="http://x", api_key="", model="m"))[0]
        )
        self.assertFalse(
            ai_client.validate_config(ai_client.AIConfig(base_url="http://x", api_key="k", model=""))[0]
        )

    def test_http_error(self) -> None:
        def handler_override(handler_self, *args):
            pass

        # 直接构造一个返回 401 的临时服务
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class UnauthorizedHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                data = json.dumps({"error": {"message": "invalid api key"}}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), UnauthorizedHandler)
        import threading

        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            config = ai_client.AIConfig(
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key="bad",
                model="m",
            )
            with self.assertRaises(ai_client.AIError) as ctx:
                ai_client.ask_question(b"x", config)
            self.assertIn("401", str(ctx.exception))
        finally:
            server.shutdown()
            server.server_close()

    def test_parse_answer_variants(self) -> None:
        cases = {
            "推理内容\n答案：C": "C",
            "最终答案: d": "D",
            "正确答案：（B）": "B",
            "The answer is A": "A",
            "选项：F": "F",
            "根据分析，选 B": "B",
            "答案：Ａ": "A",
        }
        for text, expected in cases.items():
            self.assertEqual(ai_client.parse_answer(text), expected, text)

    def test_parse_answers_multi(self) -> None:
        self.assertEqual(ai_client.parse_answers("答案：A、C"), ["A", "C"])
        self.assertEqual(ai_client.parse_answers("答案：AC"), ["A", "C"])
        self.assertEqual(ai_client.parse_answers("正确答案：B, D, E"), ["B", "D", "E"])
        self.assertEqual(ai_client.parse_answers("答案：A"), ["A"])

    def test_parse_answer_failure(self) -> None:
        with self.assertRaises(ai_client.AIError):
            ai_client.parse_answer("我不知道")
        with self.assertRaises(ai_client.AIError):
            ai_client.parse_answer("")

    def test_no_answer_content(self) -> None:
        # 修改模拟服务的返回内容
        self.server.answer = "?"  # 仍会生成"答案：?"，无法解析
        with self.assertRaises(ai_client.AIError):
            ai_client.ask_question(b"x", self.config)


class AIConfigDefaultsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        config = ai_client.AIConfig()
        self.assertTrue(config.base_url.startswith("https://"))
        self.assertTrue(config.model)
        self.assertEqual(config.temperature, 0.0)
        self.assertFalse(config.enable_thinking)

    def test_is_deepseek_endpoint(self) -> None:
        self.assertTrue(ai_client.is_deepseek_endpoint("https://api.deepseek.com"))
        self.assertTrue(ai_client.is_deepseek_endpoint("https://api.deepseek.com/v1"))
        self.assertFalse(ai_client.is_deepseek_endpoint("https://open.bigmodel.cn/api/paas/v4"))
        self.assertFalse(ai_client.is_deepseek_endpoint(""))
        self.assertFalse(ai_client.is_deepseek_endpoint("not-a-url"))

    def test_deepseek_model_alias(self) -> None:
        self.assertEqual(
            ai_client.normalize_model_name("https://api.deepseek.com", "deepseek-v4-flash"),
            "deepseek-flash",
        )
        self.assertEqual(
            ai_client.normalize_model_name("https://api.deepseek.com", "deepseek-flash"),
            "deepseek-flash",
        )
        with self.assertRaises(ai_client.AIError):
            ai_client.normalize_model_name("https://api.deepseek.com", "unknown-model")

    def test_other_provider_model_unchanged(self) -> None:
        self.assertEqual(
            ai_client.normalize_model_name(
                "https://open.bigmodel.cn/api/paas/v4", "glm-4v-flash"
            ),
            "glm-4v-flash",
        )


class AIOptionLayoutTest(unittest.TestCase):
    def _config(self, content: str):
        server, base_url = start_mock_ai_server(content=content)
        self.addCleanup(lambda: stop_mock_ai_server(server))
        return ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=10
        )

    def test_detect_layout_valid(self) -> None:
        content = (
            '{"options":[{"letter":"A","x":0.10,"y":0.60},'
            '{"letter":"B","x":0.12,"y":0.70},'
            '{"letter":"C","x":0.14,"y":0.80}],'
            '"next":{"x":0.86,"y":0.93}}'
        )
        layout = ai_client.detect_option_layout(b"fake-png", self._config(content))
        self.assertEqual(layout.points["A"], (0.10, 0.60))
        self.assertEqual(layout.points["C"], (0.14, 0.80))
        self.assertEqual(layout.next_point, (0.86, 0.93))
        self.assertGreaterEqual(layout.elapsed_ms, 0)

    def test_detect_layout_fenced_json(self) -> None:
        content = (
            "```json\n"
            '{"options":[{"letter":"A","x":0.1,"y":0.6},{"letter":"D","x":0.2,"y":0.9}],'
            '"next":null}\n```'
        )
        layout = ai_client.detect_option_layout(b"fake-png", self._config(content))
        self.assertEqual(set(layout.points), {"A", "D"})
        self.assertIsNone(layout.next_point)

    def test_parse_layout_dict_form(self) -> None:
        points, next_point = ai_client.parse_option_layout(
            {"options": {"A": {"x": 0.1, "y": 0.2}, "B": {"x": 0.3, "y": 0.4}}, "next": None}
        )
        self.assertEqual(points["B"], (0.3, 0.4))
        self.assertIsNone(next_point)

    def test_detect_layout_invalid_content(self) -> None:
        layout = self._config("这不是 JSON，AI 输出了解释文字")
        with self.assertRaises(ai_client.AIError):
            ai_client.detect_option_layout(b"fake-png", layout)

    def test_detect_layout_too_few_options(self) -> None:
        content = '{"options":[{"letter":"A","x":0.1,"y":0.6}],"next":{"x":0.8,"y":0.9}}'
        with self.assertRaises(ai_client.AIError):
            ai_client.detect_option_layout(b"fake-png", self._config(content))

    def test_ask_with_layout_json(self) -> None:
        content = (
            '{"answer":"C","options":['
            '{"letter":"A","x":0.10,"y":0.55},{"letter":"B","x":0.10,"y":0.65},'
            '{"letter":"C","x":0.10,"y":0.75},{"letter":"D","x":0.10,"y":0.85}],'
            '"next":{"x":0.88,"y":0.94}}'
        )
        answer = ai_client.ask_with_layout(b"fake-png", self._config(content))
        self.assertEqual(answer.letter, "C")
        self.assertEqual(answer.norm_points["C"], (0.10, 0.75))
        self.assertEqual(answer.norm_next, (0.88, 0.94))

    def test_ask_with_layout_fallback_text(self) -> None:
        answer = ai_client.ask_with_layout(
            b"fake-png", self._config("推理：B 正确。\n答案：B")
        )
        self.assertEqual(answer.letter, "B")
        self.assertEqual(answer.norm_points, {})
        self.assertIsNone(answer.norm_next)

    def test_ask_with_layout_answers_array(self) -> None:
        content = (
            '{"type":"multiple","answers":["A","C"],"options":['
            '{"letter":"A","x":0.10,"y":0.55},{"letter":"B","x":0.10,"y":0.65},'
            '{"letter":"C","x":0.10,"y":0.75},{"letter":"D","x":0.10,"y":0.85}],'
            '"next":{"x":0.88,"y":0.94}}'
        )
        answer = ai_client.ask_with_layout(b"fake-png", self._config(content))
        self.assertEqual(answer.all_letters(), ["A", "C"])
        self.assertEqual(answer.letter, "A")

    def test_parse_answers_multi_legacy_text(self) -> None:
        answer = ai_client.ask_with_layout(
            b"fake-png", self._config("推理：多选题选 A 和 C。\n答案：A、C")
        )
        self.assertEqual(answer.all_letters(), ["A", "C"])


class AIPageTest(unittest.TestCase):
    def _config(self, content: str):
        server, base_url = start_mock_ai_server(content=content)
        self.addCleanup(lambda: stop_mock_ai_server(server))
        return ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=10
        )

    def test_ask_page_valid(self) -> None:
        content = (
            '{"questions":['
            '{"index":1,"answer":"B","options":[{"letter":"A","x":0.1,"y":0.2},{"letter":"B","x":0.1,"y":0.3}]},'
            '{"index":2,"answer":"C","options":[{"letter":"A","x":0.1,"y":0.5},{"letter":"B","x":0.1,"y":0.6},{"letter":"C","x":0.1,"y":0.7}]}],'
            '"next":{"x":0.9,"y":0.95}}'
        )
        page = ai_client.ask_page(b"fake-png", self._config(content))
        self.assertEqual(len(page.questions), 2)
        self.assertEqual(page.questions[0].index, 1)
        self.assertEqual(page.questions[0].letter, "B")
        self.assertEqual(page.questions[1].letter, "C")
        self.assertEqual(page.questions[1].norm_points["C"], (0.1, 0.7))
        self.assertEqual(page.norm_next, (0.9, 0.95))

    def test_ask_page_multiple_answers(self) -> None:
        content = (
            '{"questions":[{'
            '"index":1,"type":"multiple","answers":["A","C"],"options":['
            '{"letter":"A","x":0.1,"y":0.2},{"letter":"B","x":0.1,"y":0.3},'
            '{"letter":"C","x":0.1,"y":0.4},{"letter":"D","x":0.1,"y":0.5}]}]}'
        )
        page = ai_client.ask_page(b"fake-png", self._config(content))
        self.assertEqual(len(page.questions), 1)
        self.assertEqual(page.questions[0].letters, ["A", "C"])

    def test_ask_page_key_and_answered(self) -> None:
        content = (
            '{"questions":[{"index":1,"key":"中国的首都是哪里","answered":true,'
            '"answers":["B"],"options":[{"letter":"A","x":0.1,"y":0.2},{"letter":"B","x":0.1,"y":0.3}]},'
            '{"index":2,"key":"3 + 4 = ?","answered":false,"answers":["C"],"selected":["C"],'
            '"options":[{"letter":"A","x":0.1,"y":0.4},{"letter":"C","x":0.1,"y":0.6}]}]}'
        )
        page = ai_client.ask_page(b"fake-png", self._config(content))
        self.assertEqual(len(page.questions), 2)
        self.assertEqual(page.questions[0].key, "中国的首都是哪里")
        self.assertTrue(page.questions[0].answered)
        self.assertEqual(page.questions[1].key, "3 + 4 = ?")
        self.assertFalse(page.questions[1].answered)
        self.assertEqual(page.questions[1].selected, ["C"])

    def test_ask_page_empty_questions(self) -> None:
        page = ai_client.ask_page(
            b"fake-png", self._config('{"questions":[],"next":null}')
        )
        self.assertEqual(page.questions, [])
        self.assertIsNone(page.norm_next)

    def test_ask_page_skips_invalid_question(self) -> None:
        content = (
            '{"questions":['
            '{"index":1,"answer":"","options":[{"letter":"A","x":0.1,"y":0.2}]},'
            '{"index":2,"answer":"D","options":[{"letter":"A","x":0.1,"y":0.4},{"letter":"D","x":0.1,"y":0.5}]}]}'
        )
        page = ai_client.ask_page(b"fake-png", self._config(content))
        self.assertEqual(len(page.questions), 1)
        self.assertEqual(page.questions[0].letter, "D")

    def test_ask_page_invalid_json(self) -> None:
        with self.assertRaises(ai_client.AIError):
            ai_client.ask_page(b"fake-png", self._config("不是 JSON"))


if __name__ == "__main__":
    unittest.main()
