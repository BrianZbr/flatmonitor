"""
Unit tests for main.py
Tests the FlatMonitor orchestrator and config reload functionality.
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch

from app.main import FlatMonitor


class TestFlatMonitor:
    """Tests for FlatMonitor class."""

    def test_yaml_import_available(self):
        """Test that yaml module is properly imported in main.py.
        
        This is a regression test for: name 'yaml' is not defined error.
        """
        import app.main as main_module
        assert hasattr(main_module, 'yaml'), "yaml module should be imported in main.py"

    def test_reload_dashboard_config_updates_settings(self, tmp_path):
        """Test that _reload_dashboard_config actually updates renderer config from YAML.
        
        This directly tests the method that was failing due to missing yaml import.
        """
        # Create a temporary config file
        config_path = tmp_path / "config.yaml"
        initial_config = """settings:
  dashboard:
    title: "Initial Title"
    header_text: "Initial Header"
    announcement: "Initial Announcement"
    footer_links:
      - text: "Initial Link"
        url: "https://initial.com"
    favicon: "initial.ico"
    logo: "initial.png"
    sort_by: "yaml_order"
    auto_refresh_seconds: 120

domains:
  - id: test.site
    url: https://example.com
"""
        config_path.write_text(initial_config)
        
        # Create FlatMonitor instance without starting the main loop
        monitor = FlatMonitor(
            config_path=str(config_path),
            data_dir=str(tmp_path / "data"),
            output_dir=str(tmp_path / "public")
        )
        
        # Mock the components to avoid full initialization
        monitor.config_loader = Mock()
        monitor.config_loader.dashboard = Mock()
        monitor.config_loader.dashboard.title = "Fallback Title"
        monitor.config_loader.dashboard.header_text = "Fallback Header"
        monitor.config_loader.dashboard.announcement = None
        monitor.config_loader.dashboard.footer_links = []
        monitor.config_loader.dashboard.favicon = None
        monitor.config_loader.dashboard.logo = None
        monitor.config_loader.dashboard.sort_by = "yaml_order"
        monitor.config_loader.dashboard.auto_refresh_seconds = 0
        
        # Create a mock renderer with a real dict for dashboard_config
        monitor.renderer = Mock()
        monitor.renderer.dashboard_config = {
            'title': 'Initial',
            'header_text': None,
            'announcement': None,
            'footer_links': [],
            'favicon': None,
            'logo': None,
            'sort_by': 'yaml_order',
            'auto_refresh_seconds': 0
        }
        
        # Call the method under test - this would fail with NameError if yaml not imported
        monitor._reload_dashboard_config()
        
        # Verify the config was loaded and applied
        assert monitor.renderer.dashboard_config['title'] == "Initial Title"
        assert monitor.renderer.dashboard_config['header_text'] == "Initial Header"
        assert monitor.renderer.dashboard_config['announcement'] == "Initial Announcement"
        assert len(monitor.renderer.dashboard_config['footer_links']) == 1
        assert monitor.renderer.dashboard_config['footer_links'][0]['text'] == "Initial Link"
        assert monitor.renderer.dashboard_config['favicon'] == "initial.ico"
        assert monitor.renderer.dashboard_config['logo'] == "initial.png"
        assert monitor.renderer.dashboard_config['sort_by'] == "yaml_order"
        assert monitor.renderer.dashboard_config['auto_refresh_seconds'] == 120

    def test_reload_dashboard_config_missing_file(self, tmp_path):
        """Test that _reload_dashboard_config handles missing config file gracefully."""
        config_path = tmp_path / "config.yaml"
        
        # Create initial config
        initial_config = """settings:
  dashboard:
    title: "Test Title"

domains:
  - id: test.site
    url: https://example.com
"""
        config_path.write_text(initial_config)
        
        monitor = FlatMonitor(config_path=str(config_path))
        monitor.config_loader = Mock()
        monitor.config_loader.dashboard = Mock()
        monitor.config_loader.dashboard.title = "Fallback"
        monitor.config_loader.dashboard.header_text = None
        monitor.config_loader.dashboard.announcement = None
        monitor.config_loader.dashboard.footer_links = []
        monitor.config_loader.dashboard.favicon = None
        monitor.config_loader.dashboard.logo = None
        monitor.config_loader.dashboard.sort_by = "yaml_order"
        monitor.config_loader.dashboard.auto_refresh_seconds = 0
        
        original_config = {'title': 'Original', 'header_text': None, 'announcement': None,
                          'footer_links': [], 'favicon': None, 'logo': None, 'sort_by': 'yaml_order',
                          'auto_refresh_seconds': 0}
        monitor.renderer = Mock()
        monitor.renderer.dashboard_config = original_config.copy()
        
        # Delete the config file
        config_path.unlink()
        
        # Should not raise exception, just return early
        monitor._reload_dashboard_config()
        
        # Config should remain unchanged
        assert monitor.renderer.dashboard_config == original_config

    def test_reload_dashboard_config_invalid_yaml(self, tmp_path):
        """Test that _reload_dashboard_config handles invalid YAML gracefully."""
        config_path = tmp_path / "config.yaml"
        
        # Create initial valid config
        initial_config = """settings:
  dashboard:
    title: "Test Title"

domains:
  - id: test.site
    url: https://example.com
"""
        config_path.write_text(initial_config)
        
        monitor = FlatMonitor(config_path=str(config_path))
        monitor.config_loader = Mock()
        monitor.config_loader.dashboard = Mock()
        monitor.config_loader.dashboard.title = "Fallback"
        monitor.config_loader.dashboard.header_text = None
        monitor.config_loader.dashboard.announcement = None
        monitor.config_loader.dashboard.footer_links = []
        monitor.config_loader.dashboard.favicon = None
        monitor.config_loader.dashboard.logo = None
        monitor.config_loader.dashboard.sort_by = "yaml_order"
        monitor.config_loader.dashboard.auto_refresh_seconds = 0
        
        original_config = {'title': 'Original', 'header_text': None, 'announcement': None,
                          'footer_links': [], 'favicon': None, 'logo': None, 'sort_by': 'yaml_order',
                          'auto_refresh_seconds': 0}
        monitor.renderer = Mock()
        monitor.renderer.dashboard_config = original_config.copy()
        
        # Overwrite with invalid YAML
        config_path.write_text("invalid: yaml: content: [")
        
        # Should catch the exception and log a warning, not propagate the error
        with patch('app.main.logger') as mock_logger:
            monitor._reload_dashboard_config()
            # Verify a warning was logged
            mock_logger.warning.assert_called_once()
            assert "Failed to reload dashboard config" in str(mock_logger.warning.call_args)
        
        # Config should remain unchanged after failed reload
        assert monitor.renderer.dashboard_config == original_config

    def test_upload_static_files_uploads_configured_files(self, tmp_path):
        """_upload_static_files uploads each file in static_files list."""
        output_dir = tmp_path / "public"
        output_dir.mkdir()
        dmca = output_dir / "dmca.html"
        dmca.write_text("<html>DMCA Notice</html>")
        robots = output_dir / "robots.txt"
        robots.write_text("User-agent: *\nDisallow: /")

        monitor = FlatMonitor()
        monitor.config_loader = Mock()
        monitor.config_loader.dashboard.static_files = ["dmca.html", "robots.txt"]
        monitor.output_dir = str(output_dir)

        mock_backend = Mock()
        monitor._upload_static_files(mock_backend)

        assert mock_backend.write_file.call_count == 2
        dmca_call = mock_backend.write_file.call_args_list[0]
        assert dmca_call[0][0] == "dmca.html"
        assert "<html>DMCA Notice</html>" in dmca_call[0][1]
        assert dmca_call[1]['content_type'] == "text/html"

        robots_call = mock_backend.write_file.call_args_list[1]
        assert robots_call[0][0] == "robots.txt"
        assert "User-agent:" in robots_call[0][1]

    def test_upload_static_files_skips_missing_files(self, tmp_path):
        """_upload_static_files skips files that don't exist."""
        output_dir = tmp_path / "public"
        output_dir.mkdir()
        # Only create robots.txt, not dmca.html
        robots = output_dir / "robots.txt"
        robots.write_text("User-agent: *")

        monitor = FlatMonitor()
        monitor.config_loader = Mock()
        monitor.config_loader.dashboard.static_files = ["dmca.html", "robots.txt"]
        monitor.output_dir = str(output_dir)

        mock_backend = Mock()
        with patch('app.main.logger') as mock_logger:
            monitor._upload_static_files(mock_backend)

        # Only robots should be uploaded
        mock_backend.write_file.assert_called_once()
        assert mock_backend.write_file.call_args[0][0] == "robots.txt"
        # Warning should be logged for dmca.html
        mock_logger.warning.assert_called_once()

    def test_upload_static_files_empty_list_does_nothing(self, tmp_path):
        """_upload_static_files is a no-op with empty static_files."""
        output_dir = tmp_path / "public"
        output_dir.mkdir()

        monitor = FlatMonitor()
        monitor.config_loader = Mock()
        monitor.config_loader.dashboard.static_files = []
        monitor.output_dir = str(output_dir)

        mock_backend = Mock()
        monitor._upload_static_files(mock_backend)

        mock_backend.write_file.assert_not_called()
