"""
FlatMonitor - Main Orchestrator

Manages queues and the main loop for the monitoring system.
"""

import time
import logging
import signal
import sys
import traceback
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue, Empty
from threading import Thread, Lock
from typing import Optional

from app.models import DomainConfig, Result
from app.config import ConfigLoader
from app.scheduler import Scheduler
from app.runner import Runner
from app.storage import Storage
from app.aggregator import Aggregator
from app.renderer import Renderer
from app.storage_backends import create_storage_backend


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FlatMonitor:
    """Main orchestrator for the monitoring system."""

    def __init__(self, config_path: str = "config/domains.yaml",
                 data_dir: str = "data", output_dir: str = "public",
                 worker_count: int = 10, rotation_interval: Optional[int] = None):
        self.config_path = config_path
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.worker_count = worker_count
        self._rotation_interval_param = rotation_interval  # Store constructor override

        # Queues
        self.job_queue: Queue = Queue()
        self.results_queue: Queue = Queue()

        # Components
        self.config_loader: Optional[ConfigLoader] = None
        self.scheduler: Optional[Scheduler] = None
        self.storage: Optional[Storage] = None
        self.aggregator: Optional[Aggregator] = None
        self.renderer: Optional[Renderer] = None

        # Threading
        self.workers: list = []
        self.running = False
        self.worker_lock = Lock()

        # Rotation tracking
        self.last_rotation = time.time()
        self.rotation_interval = 86400  # Will be overridden by config or constructor param (default: daily)

        # Shutdown flag
        self.shutdown_requested = False

    def start(self) -> None:
        """Initialize and start the monitoring system."""
        logger.info("Starting FlatMonitor...")

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Load configuration
        self._load_config()

        # Initialize components
        self.storage = Storage(data_dir=self.data_dir, retention_days=self.config_loader.retention_days)
        self.aggregator = Aggregator(bucket_minutes=5, history_hours=4, data_dir=self.data_dir)

        # Create storage backend for dashboard output (R2, S3, or filesystem)
        storage_backend = create_storage_backend(self.config_loader.storage.__dict__)
        logger.info(f"Using storage backend: {self.config_loader.storage.type}")

        dashboard_config = {
            'title': self.config_loader.dashboard.title,
            'header_text': self.config_loader.dashboard.header_text,
            'announcement': self.config_loader.dashboard.announcement,
            'footer_links': self.config_loader.dashboard.footer_links,
            'favicon': self.config_loader.dashboard.favicon,
            'logo': self.config_loader.dashboard.logo,
            'header_hint': self.config_loader.dashboard.header_hint,
            'footer_explanation': self.config_loader.dashboard.footer_explanation
        }
        self.renderer = Renderer(
            output_dir=self.output_dir,
            noindex=self.config_loader.noindex,
            dashboard_config=dashboard_config,
            storage_backend=storage_backend
        )

        # Initialize scheduler with loaded domains
        self.scheduler = Scheduler(self.config_loader.domains)

        # Start worker threads
        self.running = True
        self._start_workers()

        # Run main loop
        self._main_loop()

    def _load_config(self) -> None:
        """Load domain configuration."""
        self.config_loader = ConfigLoader(self.config_path)
        domains = self.config_loader.load()
        logger.info(f"Loaded {len(domains)} domains from configuration")
        
        # Use constructor override, or config value, or default (in that order)
        if self._rotation_interval_param is not None:
            self.rotation_interval = self._rotation_interval_param
            logger.info(f"Using constructor rotation_interval: {self.rotation_interval}s")
        else:
            self.rotation_interval = self.config_loader.rotation_interval
            logger.info(f"Using config rotation_interval: {self.rotation_interval}s")

    def _start_workers(self) -> None:
        """Start worker threads to process jobs."""
        for i in range(self.worker_count):
            worker = Thread(target=self._worker_loop, args=(i,), daemon=True)
            worker.start()
            self.workers.append(worker)

        logger.info(f"Started {self.worker_count} worker threads")

    def _worker_loop(self, worker_id: int) -> None:
        """Worker thread that pulls from job_queue and pushes to results_queue."""
        runner = Runner(data_dir=self.data_dir)
        logger.info(f"Worker {worker_id} started")

        while self.running:
            try:
                # Get job from queue with timeout
                domain = self.job_queue.get(timeout=1.0)

                # Execute check
                result = runner.check(domain)

                # Push to results queue
                self.results_queue.put(result)

                # Mark job as done
                self.job_queue.task_done()

            except Empty:
                # Queue timeout - normal behavior when no jobs pending
                pass
            except Exception as e:
                if self.running:
                    logger.error(f"Worker {worker_id} error: {e}")
                    logger.debug(f"Worker {worker_id} traceback: {traceback.format_exc()}")

        runner.close()
        logger.info(f"Worker {worker_id} stopped")

    def _main_loop(self) -> None:
        """Main loop that orchestrates the monitoring system."""
        logger.info("Main loop started")
        new_data = False

        while not self.shutdown_requested:
            try:
                # 1. Schedule checks
                jobs_added = self.scheduler.tick(self.job_queue)
                if jobs_added > 0:
                    logger.debug(f"Scheduled {jobs_added} jobs")

                # 2. Process results (Single-threaded writing)
                while not self.results_queue.empty():
                    try:
                        result = self.results_queue.get(timeout=0.1)
                        self.storage.append_csv(result)
                        new_data = True
                        self.results_queue.task_done()
                    except Exception as e:
                        logger.error(f"Error processing result: {e}")

                # 3. Check if rotation is needed
                self._check_rotation()

                # 4. Aggregation & Rendering (Throttled)
                if new_data and self.renderer.should_rebuild():
                    logger.info("Rebuilding dashboard...")
                    # Reload dashboard config to pick up changes without restart
                    self._reload_dashboard_config()
                    aggregated = self.aggregator.process_recent_data(
                        self.storage,
                        self.config_loader.get_sites()
                    )
                    self.renderer.build_static_site(aggregated)

                    # Upload logs if enabled (for R2/S3 backends)
                    if self.config_loader.storage.upload_logs:
                        storage_backend = self.renderer.storage
                        data_path = Path(self.data_dir)
                        storage_backend.upload_logs(data_path)

                    # Upload assets if using cloud storage (R2/S3 backends)
                    if self.config_loader.storage.type in ('r2', 's3'):
                        storage_backend = self.renderer.storage
                        assets_path = Path(self.output_dir) / "assets"
                        logger.info(f"Uploading assets from {assets_path} (exists: {assets_path.exists()})")
                        storage_backend.upload_assets(assets_path)

                    new_data = False
                    logger.info("Dashboard rebuilt")

                # Sleep to prevent busy-waiting
                time.sleep(1)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(1)

        # Shutdown
        self._shutdown()

    def _reload_dashboard_config(self) -> None:
        """Reload dashboard config from YAML to pick up changes without restart.
        
        Only dashboard settings are reloaded (safe to change anytime).
        Domain and storage changes still require a restart.
        """
        try:
            if not Path(self.config_path).exists():
                logger.warning(f"Config file not found: {self.config_path}")
                return

            with open(self.config_path, "r") as f:
                config_data = yaml.safe_load(f) or {}

            settings = config_data.get('settings', {})
            dashboard_settings = settings.get('dashboard', {})

            # Update renderer's dashboard config with fresh values
            self.renderer.dashboard_config = {
                'title': dashboard_settings.get('title', self.config_loader.dashboard.title),
                'header_text': dashboard_settings.get('header_text', self.config_loader.dashboard.header_text),
                'announcement': dashboard_settings.get('announcement', self.config_loader.dashboard.announcement),
                'footer_links': dashboard_settings.get('footer_links', self.config_loader.dashboard.footer_links),
                'favicon': dashboard_settings.get('favicon', self.config_loader.dashboard.favicon),
                'logo': dashboard_settings.get('logo', self.config_loader.dashboard.logo),
                'sort_by': dashboard_settings.get('sort_by', self.config_loader.dashboard.sort_by),
                'header_hint': dashboard_settings.get('header_hint', self.config_loader.dashboard.header_hint),
                'footer_explanation': dashboard_settings.get('footer_explanation', self.config_loader.dashboard.footer_explanation)
            }

            logger.debug("Dashboard config reloaded")
        except Exception as e:
            logger.warning(f"Failed to reload dashboard config: {e}")

    def _check_rotation(self) -> None:
        """Check if log rotation is needed."""
        now = time.time()
        if now - self.last_rotation >= self.rotation_interval:
            logger.info("Performing log rotation...")
            self.storage.rotate()
            self.storage.cleanup()
            self.last_rotation = now
            logger.info("Log rotation complete")

    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self.shutdown_requested = True

    def _shutdown(self) -> None:
        """Gracefully shutdown the system."""
        logger.info("Shutting down...")

        # Stop accepting new jobs
        self.running = False

        # Wait for workers to finish current jobs
        logger.info("Waiting for workers to complete...")
        for worker in self.workers:
            worker.join(timeout=5.0)

        # Process any remaining results
        while not self.results_queue.empty():
            try:
                result = self.results_queue.get(timeout=0.1)
                self.storage.append_csv(result)
            except Exception:
                break

        # Clear Python cache to prevent stale bytecode on restart
        self._clear_python_cache()

        # Final dashboard build
        logger.info("Building final dashboard...")
        aggregated = self.aggregator.process_recent_data(
            self.storage,
            self.config_loader.get_sites()
        )
        self.renderer.build_static_site(aggregated)

        # Final log upload if enabled
        if self.config_loader.storage.upload_logs:
            storage_backend = self.renderer.storage
            data_path = Path(self.data_dir)
            storage_backend.upload_logs(data_path)

    def _clear_python_cache(self) -> None:
        """Clear __pycache__ directories to prevent stale bytecode issues."""
        import shutil
        from pathlib import Path

        cache_dirs_removed = 0
        project_root = Path(__file__).parent.parent

        for pycache_dir in project_root.rglob("__pycache__"):
            if pycache_dir.is_dir():
                try:
                    shutil.rmtree(pycache_dir)
                    cache_dirs_removed += 1
                except Exception:
                    pass  # Ignore permission errors

        if cache_dirs_removed > 0:
            logger.info(f"Cleared {cache_dirs_removed} __pycache__ directories")

        logger.info("Shutdown complete")
        sys.exit(0)


def main():
    """Entry point for FlatMonitor."""
    monitor = FlatMonitor()
    monitor.start()


if __name__ == "__main__":
    main()
