"""
FlatMonitor - Health Check Module

Minimal health tracking and HTTP endpoint for monitoring core functionality.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class HealthChecker:
    """
    Tracks health of core operations (logging, uploading).
    
    Simple approach: track consecutive failures for each component.
    Mark unhealthy after N consecutive failures, recover on first success.
    """

    def __init__(self, failure_threshold: int = 3):
        self.failure_threshold = failure_threshold

        # Write health tracking
        self._write_failures = 0
        self._write_last_error: Optional[str] = None
        self._write_last_attempt: Optional[str] = None

        # Upload health tracking
        self._upload_failures = 0
        self._upload_last_error: Optional[str] = None
        self._upload_last_attempt: Optional[str] = None

        # Heartbeat health tracking
        self._heartbeat_failures = 0
        self._heartbeat_last_error: Optional[str] = None
        self._heartbeat_last_attempt: Optional[str] = None

        # Overall health
        self._healthy = True
        self._unhealthy_reason: Optional[str] = None

        # Thread safety
        self._lock = threading.Lock()

    def report_write_success(self) -> None:
        """Report a successful write operation."""
        with self._lock:
            self._write_failures = 0
            self._write_last_error = None
            self._write_last_attempt = datetime.now(timezone.utc).isoformat()
            self._update_health()

    def report_write_failure(self, error: str) -> None:
        """Report a write failure."""
        with self._lock:
            self._write_failures += 1
            self._write_last_error = error
            self._write_last_attempt = datetime.now(timezone.utc).isoformat()
            self._update_health()

    def report_upload_success(self) -> None:
        """Report a successful upload operation."""
        with self._lock:
            self._upload_failures = 0
            self._upload_last_error = None
            self._upload_last_attempt = datetime.now(timezone.utc).isoformat()
            self._update_health()

    def report_upload_failure(self, error: str) -> None:
        """Report an upload failure."""
        with self._lock:
            self._upload_failures += 1
            self._upload_last_error = error
            self._upload_last_attempt = datetime.now(timezone.utc).isoformat()
            self._update_health()

    def report_heartbeat_success(self) -> None:
        """Report a successful heartbeat."""
        with self._lock:
            self._heartbeat_failures = 0
            self._heartbeat_last_error = None
            self._heartbeat_last_attempt = datetime.now(timezone.utc).isoformat()
            self._update_health()

    def report_heartbeat_failure(self, error: str) -> None:
        """Report a heartbeat failure."""
        with self._lock:
            self._heartbeat_failures += 1
            self._heartbeat_last_error = error
            self._heartbeat_last_attempt = datetime.now(timezone.utc).isoformat()
            self._update_health()

    def _update_health(self) -> None:
        """Update overall health status based on component health."""
        if self._write_failures >= self.failure_threshold:
            self._healthy = False
            self._unhealthy_reason = (
                f"Write failures: {self._write_failures} consecutive "
                f"(last error: {self._write_last_error})"
            )
        elif self._upload_failures >= self.failure_threshold:
            self._healthy = False
            self._unhealthy_reason = (
                f"Upload failures: {self._upload_failures} consecutive "
                f"(last error: {self._upload_last_error})"
            )
        elif self._heartbeat_failures >= self.failure_threshold:
            self._healthy = False
            self._unhealthy_reason = (
                f"Heartbeat failures: {self._heartbeat_failures} consecutive "
                f"(last error: {self._heartbeat_last_error})"
            )
        else:
            self._healthy = True
            self._unhealthy_reason = None

    @property
    def is_healthy(self) -> bool:
        """Check if system is healthy."""
        with self._lock:
            return self._healthy

    def get_status(self) -> dict:
        """Get health status as a dictionary."""
        with self._lock:
            return {
                "status": "healthy" if self._healthy else "unhealthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "components": {
                    "write": {
                        "healthy": self._write_failures < self.failure_threshold,
                        "consecutive_failures": self._write_failures,
                        "last_error": self._write_last_error,
                        "last_attempt": self._write_last_attempt,
                    },
                    "upload": {
                        "healthy": self._upload_failures < self.failure_threshold,
                        "consecutive_failures": self._upload_failures,
                        "last_error": self._upload_last_error,
                        "last_attempt": self._upload_last_attempt,
                    },
                    "heartbeat": {
                        "healthy": self._heartbeat_failures < self.failure_threshold,
                        "consecutive_failures": self._heartbeat_failures,
                        "last_error": self._heartbeat_last_error,
                        "last_attempt": self._heartbeat_last_attempt,
                    },
                },
                "unhealthy_reason": self._unhealthy_reason,
            }

    def get_status_json(self) -> str:
        """Get health status as JSON string."""
        return json.dumps(self.get_status())


class HealthHandler(BaseHTTPRequestHandler):
    """Simple HTTP request handler for health endpoint."""

    health_checker: HealthChecker = None  # Set by server

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/health" or self.path == "/":
            self._handle_health()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def _handle_health(self):
        """Handle health check requests."""
        if self.health_checker is None:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Health checker not initialized"}).encode())
            return

        healthy = self.health_checker.is_healthy
        status_code = 200 if healthy else 503

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(self.health_checker.get_status_json().encode())

    def log_message(self, format, *args):
        """Suppress default logging."""
        logger.debug(f"Health endpoint: {format % args}")


class HeartbeatPinger:
    """Sends periodic heartbeat GET requests to a configured URL."""

    def __init__(self, url: str, interval: int, health_checker: HealthChecker):
        self.url = url
        self.interval = interval
        self.health_checker = health_checker
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the heartbeat pinger in a background thread."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="HeartbeatPinger",
        )
        self._thread.start()
        logger.info(f"Heartbeat pinger started: {self.url} every {self.interval}s")

    def stop(self) -> None:
        """Stop the heartbeat pinger."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Heartbeat pinger stopped")

    def _loop(self) -> None:
        """Main loop for sending heartbeats."""
        while not self._stop_event.is_set():
            try:
                self._send_heartbeat()
            except Exception as e:
                logger.error(f"Unexpected heartbeat error: {e}")
                self.health_checker.report_heartbeat_failure(str(e))
            self._stop_event.wait(self.interval)

    def _send_heartbeat(self) -> None:
        """Send a single heartbeat ping."""
        try:
            resp = requests.get(self.url, timeout=10)
            resp.raise_for_status()
            self.health_checker.report_heartbeat_success()
            logger.debug(f"Heartbeat sent to {self.url} (status {resp.status_code})")
        except requests.RequestException as e:
            logger.warning(f"Heartbeat failed to {self.url}: {e}")
            self.health_checker.report_heartbeat_failure(str(e))


class HealthServer:
    """
    Lightweight HTTP server for health checks.
    
    Runs in a background thread and serves the /health endpoint.
    """

    def __init__(self, port: int = 8080, failure_threshold: int = 3,
                 heartbeat_url: Optional[str] = None,
                 heartbeat_interval: int = 60):
        self.port = port
        self.health_checker = HealthChecker(failure_threshold=failure_threshold)
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_pinger: Optional[HeartbeatPinger] = None
        if heartbeat_url:
            self._heartbeat_pinger = HeartbeatPinger(
                url=heartbeat_url,
                interval=heartbeat_interval,
                health_checker=self.health_checker,
            )

    def start(self) -> None:
        """Start the health check server in a background thread."""
        try:
            self._server = HTTPServer(("0.0.0.0", self.port), HealthHandler)
            HealthHandler.health_checker = self.health_checker

            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="HealthServer"
            )
            self._thread.start()

            logger.info(f"Health check server started on port {self.port}")

            if self._heartbeat_pinger:
                self._heartbeat_pinger.start()

        except OSError as e:
            logger.warning(f"Could not start health check server on port {self.port}: {e}")
            logger.warning("Health endpoint will not be available")

    def stop(self) -> None:
        """Stop the health check server."""
        if self._heartbeat_pinger:
            self._heartbeat_pinger.stop()

        if self._server:
            self._server.shutdown()
            self._server = None

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def checker(self) -> HealthChecker:
        """Get the health checker instance."""
        return self.health_checker