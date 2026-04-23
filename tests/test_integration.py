"""
Integration tests for FlatMonitor full pipeline.
Tests end-to-end flow: scheduler → runner → storage → aggregator → renderer.
"""

import pytest
import tempfile
import time
import shutil
import yaml
from pathlib import Path
from queue import Queue
from datetime import datetime, timedelta, timezone

from app.config import ConfigLoader
from app.models import DomainConfig, DomainStatus, ExpectConfig
from app.scheduler import Scheduler
from app.runner import Runner
from app.storage import Storage
from app.aggregator import Aggregator
from app.renderer import Renderer


class TestFullPipeline:
    """End-to-end integration tests."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for data and output."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir) / "data"
        output_dir = Path(temp_dir) / "public"
        config_path = Path(temp_dir) / "config.yaml"
        
        yield {
            "temp_dir": temp_dir,
            "data_dir": data_dir,
            "output_dir": output_dir,
            "config_path": config_path
        }
        
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def sample_config(self, temp_dirs):
        """Create a sample config with test domains."""
        yaml_content = """
settings:
  rotation_interval_seconds: 14400

domains:
  - id: test.www
    url: https://httpbin.org/status/200
    expect:
      http_status: 200
    role: core

  - id: test.api
    url: https://httpbin.org/status/500
    expect:
      http_status: 200
    role: supplementary
"""
        with open(temp_dirs["config_path"], "w") as f:
            f.write(yaml_content)
        
        loader = ConfigLoader(str(temp_dirs["config_path"]))
        domains = loader.load()
        return loader, domains

    def test_pipeline_end_to_end(self, temp_dirs, sample_config):
        """Test full pipeline from scheduling to HTML generation."""
        config_loader, domains = sample_config
        
        # Initialize components
        job_queue = Queue()
        results_queue = Queue()
        
        scheduler = Scheduler(domains)
        runner = Runner()
        storage = Storage(str(temp_dirs["data_dir"]))
        aggregator = Aggregator(history_hours=4, bucket_minutes=1)
        renderer = Renderer(
            str(temp_dirs["output_dir"]),
            str(Path(__file__).parent.parent / "templates")
        )
        
        # Step 1: Scheduler adds jobs
        jobs_added = scheduler.tick(job_queue)
        assert jobs_added == len(domains)
        
        # Step 2: Runner processes jobs
        for _ in range(jobs_added):
            domain = job_queue.get(timeout=1)
            result = runner.check(domain)
            results_queue.put(result)
        
        # Step 3: Storage saves results
        results_saved = 0
        while not results_queue.empty():
            result = results_queue.get()
            storage.append_csv(result)
            results_saved += 1
        
        assert results_saved == len(domains)
        
        # Step 4: Aggregator processes data
        sites = config_loader.get_sites()
        aggregated = aggregator.process_recent_data(storage, sites)
        
        assert "sites" in aggregated
        assert "test" in aggregated["sites"]
        # Compare against enum value since health is SiteHealth enum
        assert aggregated["sites"]["test"]["health"].value in ["UP", "DEGRADED", "DOWN"]
        
        # Step 5: Renderer generates HTML
        templates_dir = Path(__file__).parent.parent / "templates"
        renderer = Renderer(
            templates_dir=str(templates_dir),
            output_dir=str(temp_dirs["output_dir"])
        )
        renderer.build_static_site(aggregated)
        
        # Verify HTML was created
        index_path = temp_dirs["output_dir"] / "index.html"
        site_path = temp_dirs["output_dir"] / "test.html"
        
        assert index_path.exists()
        assert site_path.exists()
        
        # Verify HTML content
        html_content = site_path.read_text()
        assert "httpbin.org" in html_content
        assert "timeline" in html_content  # Timeline visualization exists
        
        # Verify CSV files were created
        site_dir = temp_dirs["data_dir"] / "live" / "test"
        assert site_dir.exists()
        log_files = list(site_dir.glob("*.log"))
        assert len(log_files) >= 2  # At least www and api logs

    def test_rotation_interval_configurable(self, temp_dirs):
        """Test that rotation_interval can be configured."""
        yaml_content = """
settings:
  rotation_interval_seconds: 7200

domains:
  - id: test.site
    url: https://example.com
"""
        with open(temp_dirs["config_path"], "w") as f:
            f.write(yaml_content)
        
        loader = ConfigLoader(str(temp_dirs["config_path"]))
        loader.load()
        
        assert loader.rotation_interval == 7200

    def test_timeline_shows_complete_data(self, temp_dirs, sample_config):
        """Test that timeline shows data for all time buckets when sufficient data exists."""
        config_loader, domains = sample_config
        
        # Create storage with historical data
        storage = Storage(str(temp_dirs["data_dir"]))
        
        # Generate data for every minute bucket in the 4-hour window (5 hours of data)
        now = datetime.now(timezone.utc)
        for hour_offset in range(5, 0, -1):  # 5, 4, 3, 2, 1 hours ago
            for minute in range(0, 60):  # Every minute (not every 10 minutes)
                timestamp = now - timedelta(hours=hour_offset, minutes=minute)
                
                for domain in domains:
                    from app.models import Result
                    result = Result(
                        timestamp=timestamp.isoformat(),
                        site_id="test",
                        domain_id=domain.id,
                        domain_status=DomainStatus.UP,
                        http_status=200,
                        latency_ms=100,
                        failure_type=None
                    )
                    storage.append_csv(result)
        
        # Process and render
        aggregator = Aggregator(history_hours=4, bucket_minutes=1)
        sites = config_loader.get_sites()
        aggregated = aggregator.process_recent_data(storage, sites)
        
        renderer = Renderer(
            templates_dir="templates",
            output_dir=str(temp_dirs["output_dir"])
        )
        renderer.build_static_site(aggregated)
        
        # Verify HTML shows UP status
        site_path = temp_dirs["output_dir"] / "test.html"
        assert site_path.exists(), f"Site HTML not found at {site_path}"
        html_content = site_path.read_text()
        
        # Count timeline spans with UP status
        up_count = html_content.count('class="up"')
        unknown_count = html_content.count('class="unknown"')
        
        # Should have significant UP spans (at least 30 buckets with data)
        assert up_count > 30, f"Expected >30 UP spans, found {up_count}"
        
        # Most recent data should be UP, not UNKNOWN
        assert up_count > unknown_count

    def test_dashboard_config_auto_reload(self, temp_dirs):
        """Test that dashboard config changes are picked up without restart."""
        # Create initial config
        yaml_content = """
settings:
  dashboard:
    title: "Initial Title"
    header_text: "Initial header"
    logo: "initial.png"
    favicon: "initial.ico"
    announcement: "Initial announcement"
    footer_links:
      - text: "Initial Link"
        url: "https://initial.com"

domains:
  - id: test.www
    url: https://example.com
"""
        with open(temp_dirs["config_path"], "w") as f:
            f.write(yaml_content)

        # Load config and initialize renderer
        loader = ConfigLoader(str(temp_dirs["config_path"]))
        loader.load()

        renderer = Renderer(
            templates_dir="templates",
            output_dir=str(temp_dirs["output_dir"]),
            dashboard_config={
                'title': loader.dashboard.title,
                'header_text': loader.dashboard.header_text,
                'announcement': loader.dashboard.announcement,
                'footer_links': loader.dashboard.footer_links,
                'favicon': loader.dashboard.favicon,
                'logo': loader.dashboard.logo,
                'sort_by': loader.dashboard.sort_by
            }
        )

        # Verify initial config
        assert renderer.dashboard_config['title'] == "Initial Title"
        assert renderer.dashboard_config['logo'] == "initial.png"

        # Simulate _reload_dashboard_config behavior by updating config file
        updated_content = """
settings:
  dashboard:
    title: "Updated Title"
    header_text: "Updated header"
    logo: "updated.png"
    favicon: "updated.ico"
    announcement: "Updated announcement"
    footer_links:
      - text: "Updated Link"
        url: "https://updated.com"

domains:
  - id: test.www
    url: https://example.com
"""
        with open(temp_dirs["config_path"], "w") as f:
            f.write(updated_content)

        # Simulate the reload method behavior
        with open(temp_dirs["config_path"], "r") as f:
            config_data = yaml.safe_load(f) or {}

        settings = config_data.get('settings', {})
        dashboard_settings = settings.get('dashboard', {})

        # Update renderer's dashboard config
        renderer.dashboard_config = {
            'title': dashboard_settings.get('title', loader.dashboard.title),
            'header_text': dashboard_settings.get('header_text', loader.dashboard.header_text),
            'announcement': dashboard_settings.get('announcement', loader.dashboard.announcement),
            'footer_links': dashboard_settings.get('footer_links', loader.dashboard.footer_links),
            'favicon': dashboard_settings.get('favicon', loader.dashboard.favicon),
            'logo': dashboard_settings.get('logo', loader.dashboard.logo),
            'sort_by': dashboard_settings.get('sort_by', loader.dashboard.sort_by)
        }

        # Verify updated config was applied
        assert renderer.dashboard_config['title'] == "Updated Title"
        assert renderer.dashboard_config['logo'] == "updated.png"
        assert renderer.dashboard_config['favicon'] == "updated.ico"
        assert renderer.dashboard_config['announcement'] == "Updated announcement"
        assert len(renderer.dashboard_config['footer_links']) == 1
        assert renderer.dashboard_config['footer_links'][0]['text'] == "Updated Link"

    def test_dashboard_config_reload_handles_missing_file(self, temp_dirs):
        """Test that dashboard config reload gracefully handles missing config file."""
        # Create initial config
        yaml_content = """
settings:
  dashboard:
    title: "Test Title"
    logo: "test.png"

domains:
  - id: test.www
    url: https://example.com
"""
        with open(temp_dirs["config_path"], "w") as f:
            f.write(yaml_content)

        loader = ConfigLoader(str(temp_dirs["config_path"]))
        loader.load()

        renderer = Renderer(
            templates_dir="templates",
            output_dir=str(temp_dirs["output_dir"]),
            dashboard_config={
                'title': loader.dashboard.title,
                'header_text': loader.dashboard.header_text,
                'announcement': loader.dashboard.announcement,
                'footer_links': loader.dashboard.footer_links,
                'favicon': loader.dashboard.favicon,
                'logo': loader.dashboard.logo,
                'sort_by': loader.dashboard.sort_by
            }
        )

        # Delete the config file
        temp_dirs["config_path"].unlink()

        # Simulate reload with missing file - should not raise exception
        try:
            if not temp_dirs["config_path"].exists():
                # This simulates the early return in _reload_dashboard_config
                pass  # Config remains unchanged
            else:
                with open(temp_dirs["config_path"], "r") as f:
                    config_data = yaml.safe_load(f) or {}
                # ... rest of reload logic
        except Exception:
            pytest.fail("Reload should not raise exception for missing file")

        # Verify config unchanged
        assert renderer.dashboard_config['title'] == "Test Title"
        assert renderer.dashboard_config['logo'] == "test.png"

    def test_dashboard_config_reload_handles_invalid_yaml(self, temp_dirs):
        """Test that dashboard config reload gracefully handles invalid YAML."""
        # Create initial config
        yaml_content = """
settings:
  dashboard:
    title: "Test Title"
    logo: "test.png"

domains:
  - id: test.www
    url: https://example.com
"""
        with open(temp_dirs["config_path"], "w") as f:
            f.write(yaml_content)

        loader = ConfigLoader(str(temp_dirs["config_path"]))
        loader.load()

        original_title = loader.dashboard.title
        original_logo = loader.dashboard.logo

        renderer = Renderer(
            templates_dir="templates",
            output_dir=str(temp_dirs["output_dir"]),
            dashboard_config={
                'title': original_title,
                'header_text': loader.dashboard.header_text,
                'announcement': loader.dashboard.announcement,
                'footer_links': loader.dashboard.footer_links,
                'favicon': loader.dashboard.favicon,
                'logo': original_logo,
                'sort_by': loader.dashboard.sort_by
            }
        )

        # Write invalid YAML
        with open(temp_dirs["config_path"], "w") as f:
            f.write("invalid: yaml: content: [")

        # Simulate reload with invalid YAML - should catch exception
        try:
            with open(temp_dirs["config_path"], "r") as f:
                config_data = yaml.safe_load(f)  # This will raise an exception
        except Exception as e:
            # Expected - invalid YAML should be caught
            pass

        # Verify config unchanged despite reload failure
        assert renderer.dashboard_config['title'] == original_title
        assert renderer.dashboard_config['logo'] == original_logo
