import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.logic import utils as logs
from app.main import app


class LoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_frames_keep_context_and_omit_image_payload(self):
        @logs.trace_step("example")
        async def work(frame_ref, image_bytes):
            await asyncio.sleep(0)
            logs.event("example.result", count=2)

        with self.assertLogs("library_claim", level="INFO") as captured:
            await asyncio.gather(
                work("first.jpg", b"PRIVATE_IMAGE"),
                work("second.jpg", b"PRIVATE_IMAGE"),
            )
        events = [record.getMessage() for record in captured.records]
        self.assertEqual(len(events), 6)
        self.assertFalse(any("PRIVATE_IMAGE" in line for line in events))
        for name in ("first.jpg", "second.jpg"):
            self.assertEqual(
                sum(json.loads(line.split(" ", 1)[1])["frame_ref"] == name for line in events),
                3,
            )
        self.assertEqual(logs.current_context(), {})

    async def test_waiting_messages_stop_and_exceptions_still_propagate(self):
        @logs.trace_step("slow")
        async def work():
            await asyncio.sleep(0.015)
            raise ValueError("model unavailable")

        with (
            patch.object(logs, "WAIT_LOG_INTERVAL", 0.001),
            self.assertLogs("library_claim", level="INFO") as captured,
        ):
            with self.assertRaisesRegex(ValueError, "model unavailable"):
                await work()
            count = len(captured.records)
            await asyncio.sleep(0.005)
            self.assertEqual(len(captured.records), count)
        self.assertTrue(any("slow.waiting" in record.getMessage() for record in captured.records))
        self.assertIn("slow.failed", captured.records[-1].getMessage())
        self.assertEqual(logs.current_context(), {})

    async def test_http_status_and_request_id_are_logged(self):
        with self.assertLogs("library_claim", level="INFO") as captured:
            with TestClient(app) as client:
                response = client.get("/api/health")
                missing = client.get("/api/sweeps/missing-log-test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        request_id = response.headers["x-request-id"]
        matching = [
            record.getMessage() for record in captured.records if request_id in record.getMessage()
        ]
        self.assertTrue(any("http.received" in line for line in matching))
        self.assertTrue(any('"status": 200' in line for line in matching))
        self.assertTrue(any('"status": 404' in record.getMessage() for record in captured.records))
