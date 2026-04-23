# Quick Start

Get FlatMonitor running in under 5 minutes.

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd flatmonitor

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Create `config/domains.yaml`:

```yaml
domains:
  - id: shop.homepage
    url: https://example-shop.com
    expect:
      body_contains: "Welcome"

  - id: shop.api
    url: https://api.example-shop.com/health
```

## Running Locally

```bash
# Start monitoring (foreground)
python -m app.main

# Or run in background
python -m app.main &

# Dashboard will be generated at public/index.html
# Serve via simple HTTP server:
python -m http.server 8080 --directory public/
```

## Configuration Discovery

Use the discovery tool to identify what a site returns:

```bash
python -m app.discover https://example.com --checks 3
```

The `--checks N` option runs multiple probes to detect **sporadic protection** - some sites serve different responses from rotating servers or CDNs. The tool aggregates indicators across all checks and suggests configuration if any check found protection.

This shows:
- Actual HTTP status code
- Content snippet (first 500 chars)
- Bot protection indicators detected
- Suggested configuration

### Protection Detection Behavior

All bot protection (DDoS-Guard, Cloudflare, etc.) automatically shows as `PROTECTED` (green). No configuration needed.

### Content Verification

For sites without bot protection, use `body_contains` to verify the site hasn't been replaced with an error page:

```yaml
expect:
  body_contains: "Welcome to our site"
```

- If content found → `UP` (green)
- If content missing → `DOWN` (red)

**Note:** Content checks are skipped when bot protection is detected (protection pages don't contain site content).

**Logs show detailed checks:**
```
[CONTENT] example.www: looking_for='Welcome', found=True, content_length=179344
[PROTECTION] example.www: type='Cloudflare', detected='cloudflare', status_code=503
```

**Protection types detected:**
- **DDoS-Guard** - Detected via `Server: ddos-guard` header, `ddosguard.net` references
- **Cloudflare** - Detected via `Server: cloudflare` header, `cf-ray` header
- **AWS WAF** - Detected via `awselb`/`awsalb` headers
- **reCAPTCHA/hCaptcha** - Detected via content patterns

**Sporadic protection:** Some sites return mixed results across checks. The discovery tool will note this:
```
⚠️  Note: 'blocked' was found in some checks but not this one.
   Protection may be sporadic - this is normal for many sites.
```

## Next Steps

- [Configuration Reference](configuration.md) - Full options for `domains.yaml`
- [Deployment Guide](deployment.md) - Production setup with Docker or systemd
- [Architecture](architecture.md) - How FlatMonitor works internally
