# Configuration Reference

## DomainConfig Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | string | Yes | - | Unique identifier (format: `site.domain`) |
| `url` | string | Yes | - | URL to monitor |
| `interval_seconds` | int | No | 60 | Fixed at 60s (1 min). Not configurable. |
| `timeout_seconds` | int | No | 20 | HTTP timeout (min: 1, max: 60) |
| `expect` | object | No | `{http_status: 200}` | Expected response criteria |
| `expect.http_status` | int | No | 200 | Expected HTTP status code |
| `expect.body_contains` | string | No | - | Required content in response body |
| `expected_bot_protection.status_code` | int | No | - | Expected HTTP status when protected |
| `expected_bot_protection.indicator` | string | No | - | Expected protection indicator (body content like "checking your browser" or header-based like "cf-ray", "cloudflare", "ddos-guard") |

## Site Health Determination

- **UP**: All domains UP or PROTECTED
- **DEGRADED**: Any domain DEGRADED (1 failure in 5-min window)
- **DOWN**: Any domain DOWN, TIMEOUT, or UNKNOWN (2+ failures or no data)

## Dashboard Customization

Customize the dashboard appearance under `settings.dashboard`:

```yaml
settings:
  dashboard:
    title: "Service Status"                    # Page title (default: "FlatMonitor")
    header_text: "Monitoring dashboard"          # Optional subtitle
    logo: "logo.png"                             # Optional header logo (place in public/assets/)
    favicon: "favicon.ico"                       # Optional favicon (place in public/assets/)
    announcement: "Maintenance scheduled"         # Optional banner (plain text only)
    footer_links:                                # Optional footer links
      - text: "Status Page"
        url: "https://status.example.com"
    sort_by: "yaml_order"                      # Display order: 'yaml_order', 'severity', or 'alphabetical'
    instance_label: "US-East Primary"            # Optional label for this instance (shows in footer)
```

**Notes:**
- `logo` and `favicon`: Place image files in `public/assets/` directory
- `announcement`: Plain text only (HTML will be escaped)
- `sort_by`: Controls display order of sites and domains
  - `yaml_order` (default): Preserves strict order from config/domains.yaml
  - `severity`: Sites and domains sorted by health status severity (DOWN → DEGRADED → UP)
- `instance_label`: Useful for multi-instance setups (primary/secondary) to identify which instance generated the dashboard

**Auto-Reload:** Dashboard settings are automatically reloaded before each rebuild (every 30+ seconds). Changes to `title`, `logo`, `favicon`, `header_text`, `announcement`, `footer_links`, `sort_by`, and `instance_label` take effect without restarting the application. Domain and storage changes still require a restart.

## Storage Settings

```yaml
settings:
  rotation_interval_seconds: 86400  # Archive logs daily (default)
  retention_days: 7                 # Keep 7 days of archives
```

## Latency Threshold

Sites without `body_contains` configuration are subject to automatic latency-based degradation detection:
- If response time exceeds **3000ms** and no content validation is configured, status becomes `DEGRADED`
- This catches sites that return HTTP 200 but are extremely slow (e.g., broken redirects)
- To avoid this, add `body_contains` to verify actual site content

## Environment Variables

Override config defaults via environment:

| Variable | Description |
|----------|-------------|
| `FLATMONITOR_CONFIG` | Path to domains.yaml |
| `FLATMONITOR_DATA_DIR` | Data directory (default: `data/`) |
| `FLATMONITOR_OUTPUT_DIR` | Dashboard output (default: `public/`) |
| `FLATMONITOR_WORKERS` | Worker threads (default: 10) |

### Storage Credentials (Recommended)

Store sensitive credentials in environment variables instead of hardcoding them in `domains.yaml`. The config loader supports `${VAR_NAME}` syntax for substitution:

**Cloudflare R2:**
| Variable | Description |
|----------|-------------|
| `FLATMONITOR_R2_ACCOUNT_ID` | R2 account ID |
| `FLATMONITOR_R2_ACCESS_KEY_ID` | R2 API token access key |
| `FLATMONITOR_R2_SECRET_ACCESS_KEY` | R2 API token secret |
| `FLATMONITOR_R2_BUCKET_NAME` | R2 bucket name |

**AWS S3 / S3-Compatible:**
| Variable | Description |
|----------|-------------|
| `FLATMONITOR_AWS_ACCESS_KEY_ID` | AWS access key |
| `FLATMONITOR_AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `FLATMONITOR_S3_BUCKET_NAME` | S3 bucket name |

**Example usage in `domains.yaml`:**
```yaml
settings:
  storage:
    type: r2
    r2:
      account_id: "${FLATMONITOR_R2_ACCOUNT_ID}"
      access_key_id: "${FLATMONITOR_R2_ACCESS_KEY_ID}"
      secret_access_key: "${FLATMONITOR_R2_SECRET_ACCESS_KEY}"
      bucket_name: "${FLATMONITOR_R2_BUCKET_NAME}"
```
