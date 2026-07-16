"""Tests for the health check module (app/health.py)."""

import json
import threading
import time
from http.client import HTTPConnection
from http.server import HTTPServer

import pytest
import requests

from app.health import HealthChecker, HealthServer, HealthHandler


class TestHealthChecker:
    """Tests for HealthChecker logic."""

    def test_initial_state_is_healthy(self):
        checker = HealthChecker(failure_threshold=3)
        assert checker.is_healthy is True
        status = checker.get_status()
        assert status["status"] == "healthy"

    def test_single_write_failure_does_not_cause_unhealthy(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_write_failure("disk full")
        assert checker.is_healthy is True

    def test_consecutive_write_failures_cause_unhealthy(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_write_failure("error 1")
        checker.report_write_failure("error 2")
        checker.report_write_failure("error 3")
        assert checker.is_healthy is False
        status = checker.get_status()
        assert status["status"] == "unhealthy"
        assert "Write failures" in status["unhealthy_reason"]

    def test_write_success_recovers_from_unhealthy(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_write_failure("error 1")
        checker.report_write_failure("error 2")
        checker.report_write_failure("error 3")
        assert checker.is_healthy is False
        checker.report_write_success()
        assert checker.is_healthy is True
        assert checker.get_status()["unhealthy_reason"] is None

    def test_single_upload_failure_does_not_cause_unhealthy(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_upload_failure("timeout")
        assert checker.is_healthy is True

    def test_consecutive_upload_failures_cause_unhealthy(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_upload_failure("timeout 1")
        checker.report_upload_failure("timeout 2")
        checker.report_upload_failure("timeout 3")
        assert checker.is_healthy is False
        status = checker.get_status()
        assert status["status"] == "unhealthy"
        assert "Upload failures" in status["unhealthy_reason"]

    def test_upload_success_recovers_from_unhealthy(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_upload_failure("timeout 1")
        checker.report_upload_failure("timeout 2")
        checker.report_upload_failure("timeout 3")
        assert checker.is_healthy is False
        checker.report_upload_success()
        assert checker.is_healthy is True

    def test_write_and_upload_independent(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_upload_failure("timeout 1")
        checker.report_upload_failure("timeout 2")
        checker.report_upload_failure("timeout 3")
        assert checker.is_healthy is False
        checker.report_write_success()
        assert checker.is_healthy is False

    def test_custom_failure_threshold(self):
        checker = HealthChecker(failure_threshold=1)
        assert checker.is_healthy is True
        checker.report_write_failure("error")
        assert checker.is_healthy is False

    def test_write_last_error_and_attempt_tracking(self):
        checker = HealthChecker(failure_threshold=3)
        checker.report_write_failure("permission denied")
        status = checker.get_status()
        write_comp = status["components"]["write"]
        assert write_comp["last_error"] == "permission denied"
        assert write_comp["last_attempt"] is not None
        assert write_comp["consecutive_failures"] == 1

    def test_get_status_json(self):
        checker = HealthChecker(failure_threshold=3)
        json_str = checker.get_status_json()
        data = json.loads(json_str)
        assert data["status"] == "healthy"
        assert "components" in data
        assert "write" in data["components"]
        assert "upload" in data["components"]

    def test_concurrent_reports_are_thread_safe(self):
        checker = HealthChecker(failure_threshold=100)
        errors = []

        def report_failures():
            for _ in range(50):
                checker.report_write_failure("concurrent error")

        threads = [threading.Thread(target=report_failures) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert checker._write_failures == 250


class TestHealthServer:
    """Tests for HealthServer (HTTP endpoint)."""

    def test_health_endpoint_returns_200_when_healthy(self):
        server = HealthServer(port=0)
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
        finally:
            server.stop()

    def test_health_endpoint_returns_503_when_unhealthy(self):
        server = HealthServer(port=0, failure_threshold=1)
        server.health_checker.report_write_failure("test error")
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "unhealthy"
        finally:
            server.stop()

    def test_root_path_returns_health(self):
        server = HealthServer(port=0)
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/", timeout=2)
            assert resp.status_code == 200
        finally:
            server.stop()

    def test_unknown_path_returns_404(self):
        server = HealthServer(port=0)
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/nonexistent", timeout=2)
            assert resp.status_code == 404
            assert resp.json()["error"] == "Not found"
        finally:
            server.stop()

    def test_response_has_cache_control_no_cache(self):
        server = HealthServer(port=0)
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            assert resp.headers.get("Cache-Control") == "no-cache"
        finally:
            server.stop()

    def test_checker_property(self):
        server = HealthServer(port=0)
        assert server.checker is server.health_checker

    def test_stop_without_start_does_not_crash(self):
        server = HealthServer(port=0)
        server.stop()

    def test_double_stop_does_not_crash(self):
        server = HealthServer(port=0)
        server.start()
        time.sleep(0.1)
        server.stop()
        server.stop()

    def test_response_content_type_is_json(self):
        server = HealthServer(port=0)
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            assert resp.headers.get("Content-Type", "").startswith("application/json")
        finally:
            server.stop()

    def test_health_response_includes_write_component(self):
        server = HealthServer(port=0)
        checker = server.health_checker
        checker.report_write_failure("test error")
        try:
            server.start()
            time.sleep(0.1)
            port = server._server.server_port
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            data = resp.json()
            assert data["components"]["write"]["last_error"] == "test error"
            assert data["components"]["write"]["consecutive_failures"] == 1
        finally:
            server.stop()

    def test_server_logs_warning_on_port_conflict(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)

        server1 = HealthServer(port=0)
        server1.start()
        time.sleep(0.1)
        occupied_port = server1._server.server_port

        server2 = HealthServer(port=occupied_port)
        try:
            server2.start()
            assert any("Could not start health check server" in msg for msg in caplog.messages)
        finally:
            server1.stop()
            server2.stop()
