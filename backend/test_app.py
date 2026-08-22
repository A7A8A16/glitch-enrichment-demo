"""Tests for the Glitch two-step API.

Tests force the LLM client to fail so they are deterministic and never spend
tokens or depend on a real API key.
"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend import app as app_module


class GlitchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)

    def test_health_reports_service_ok(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_profile_question_uses_fallback_when_llm_fails(self) -> None:
        with patch.object(
            app_module.client,
            "complete_json",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ):
            response = self.client.post(
                "/profile-question",
                json={"state": {"motion_state": "still", "city": "南京"}},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("question_id", body)
        self.assertTrue(body["question"])

    def test_blank_answer_gets_low_expression_profile(self) -> None:
        with patch.object(
            app_module.client,
            "complete_json",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ):
            response = self.client.post(
                "/enrichment-task",
                json={
                    "state": {"motion_state": "still"},
                    "question_id": "break_routine",
                    "question": "你想打破什么？",
                    "answer": "   ",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile_tag"], "low_mood_or_low_expression")

    def test_gibberish_answer_gets_playful_profile(self) -> None:
        self.assertEqual(app_module.classify_answer("哈哈哈"), "playful_unpredictable")
        self.assertEqual(app_module.classify_answer("哈哈哈啊啊啊"), "playful_unpredictable")
        self.assertEqual(app_module.classify_answer("123"), "playful_unpredictable")

    def test_normal_answer_gets_grounded_profile(self) -> None:
        self.assertEqual(app_module.classify_answer("我想暂时离开电脑走走"), "answer_grounded")

    def test_local_fallback_library_has_at_least_twenty_tasks(self) -> None:
        total = sum(len(tasks) for tasks in app_module.TASK_FALLBACKS.values())
        self.assertGreaterEqual(total, 20)

    def test_task_response_rejects_overlong_answer(self) -> None:
        response = self.client.post(
            "/enrichment-task",
            json={
                "state": {},
                "question_id": "break_routine",
                "question": "你想打破什么？",
                "answer": "x" * 81,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_state_accepts_deidentified_sensor_summary(self) -> None:
        response = self.client.post(
            "/profile-question",
            json={
                "state": {
                    "motion_state": "moving",
                    "environment_noise": "normal",
                    "noise_db": 42.5,
                    "location_accuracy_meter": 120,
                }
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_profile_prompt_receives_all_environment_signals(self) -> None:
        mocked = AsyncMock(return_value={"question_id": "q", "question": "问题"})
        with patch.object(app_module.client, "complete_json", new=mocked):
            response = self.client.post("/profile-question", json={"state": {
                "weather": "多云", "temperature": "30", "motion_state": "still",
                "environment_noise": "quiet", "city": "南京",
            }})
        self.assertEqual(response.status_code, 200)
        sent = mocked.await_args.args[1]["state"]
        self.assertEqual(sent["weather"], "多云")
        self.assertEqual(sent["temperature"], "30")
        self.assertEqual(sent["motion_state"], "still")
        self.assertEqual(sent["environment_noise"], "quiet")

    def test_location_context_degrades_without_amap_key(self) -> None:
        with patch.dict(app_module.os.environ, {"AMAP_WEB_KEY": ""}, clear=False):
            response = self.client.post(
                "/location-context",
                json={"latitude": 32.06, "longitude": 118.79},
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])


if __name__ == "__main__":
    unittest.main()
