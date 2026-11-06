"""Тесты протокола мышления: экстрактор JSON и приведение типов."""

from __future__ import annotations

import unittest

from _helpers import build_agent  # noqa: F401 — гарантирует регистрацию.toolbox
from universal_agent import cast_params, extract_json


class TestExtractJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_markdown(self):
        text = "```json\n{\"thought\": \"шаг\", \"action\": {\"name\": \"finish\"}}\n```"
        parsed = extract_json(text)
        self.assertEqual(parsed["action"]["name"], "finish")

    def test_text_around_object(self):
        text = "Конечно! Вот мой ход:\n{\"thought\":\"ок\",\"action\":{\"name\":\"list_dir\"}}\nЧто дальше?"
        parsed = extract_json(text)
        self.assertEqual(parsed["thought"], "ок")

    def test_invalid_returns_empty(self):
        self.assertEqual(extract_json("просто болтовня без JSON"), {})

    def test_array_is_not_action(self):
        self.assertEqual(extract_json("[1, 2, 3]"), {})

    def test_nested_braces_survive(self):
        text = '{"thought":"x","action":{"name":"n","params":{"code":"print({1:2})"}}}'
        self.assertEqual(extract_json(text)["action"]["params"]["code"], "print({1:2})")


class TestCastParams(unittest.TestCase):
    def setUp(self):
        self.schema = {"type": "object",
                       "properties": {"a": {"type": "integer"},
                                      "b": {"type": "number"},
                                      "c": {"type": "boolean"},
                                      "d": {"type": "string"}},
                       "required": ["a"]}

    def test_string_numbers_coerced(self):
        out = cast_params(self.schema, {"a": "5", "b": "0.5"})
        self.assertEqual(out["a"], 5)
        self.assertAlmostEqual(out["b"], 0.5)

    def test_bool_coerced(self):
        self.assertIs(cast_params(self.schema, {"c": "true"})["c"], True)

    def test_unknown_keys_dropped(self):
        out = cast_params(self.schema, {"a": 1, "hallucinated": "нет"})
        self.assertNotIn("hallucinated", out)

    def test_bad_values_pass_through(self):
        out = cast_params(self.schema, {"a": "не число"})
        self.assertEqual(out["a"], "не число")  # пусть рука сама отрапортует об ошибке


if __name__ == "__main__":
    build_agent()  # init toolbox side-effect safety
    unittest.main()
