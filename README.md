# FlatMonitor

A lightweight HTTP monitoring tool that performs health checks and generates a static HTML dashboard.

## Features

- **HTTP Health Checks**: Monitor websites and APIs with configurable intervals
- **Static HTML Dashboard**: Fast, self-contained dashboard that can be hosted on any static server (Nginx, S3, etc.)
- **Concurrent Execution**: Queue-based worker pool for efficient parallel checks
- **Smart Classification**: Distinguishes between UP, DOWN, TIMEOUT, and PROTECTED states
- **Content Verification**: Primary status check based on expected content in response body
- **Protection Detection**: Auto-detects protection type (DDoS-Guard, Cloudflare, AWS WAF, reCAPTCHA, hCaptcha)
- **CSV Storage**: Append-only logs with automatic daily rotation and retention
- **Real-time Dashboard**: 4-hour rolling display with configurable buckets
- **CSV Archiving**: Unlimited daily archives with configurable retention (default: 7 days)

## Quick Start

```bash
# Clone and setup
git clone <repository-url> && cd flatmonitor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp config/domains.yaml.example config/domains.yaml
# Edit config/domains.yaml with your URLs

# Run
python -m app.main

# View dashboard at public/index.html
python -m http.server 8080 --directory public/
```

## Documentation

- **[Quick Start](docs/quickstart.md)** - Installation, first config, and discovery tool
- **[Configuration](docs/configuration.md)** - Full `domains.yaml` reference and dashboard customization
- **[Deployment](docs/deployment.md)** - Docker, systemd, Nginx, and cloud storage (R2/S3)
- **[Architecture](docs/architecture.md)** - Design decisions, data models, and system overview

## Project Structure

```
flatmonitor/
├── app/              # Core application (models, runner, storage, etc.)
├── tests/            # Unit tests
├── config/           # Configuration directory
├── data/             # CSV logs, archives, cert metadata
├── public/           # Generated dashboard
├── templates/        # Jinja2 templates
├── docs/             # Documentation
└── scripts/          # Utility scripts
```

## Testing

```bash
python -m pytest              # Run all tests
python -m pytest -v         # Verbose output
```

## License

MIT License - see [LICENSE](LICENSE) file
