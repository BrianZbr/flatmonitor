# FlatMonitor - Architecture & Design

## 1. Project Overview
A lightweight monitoring tool that performs HTTP checks and generates a static HTML dashboard. The "Static" approach ensures the dashboard is fast and can be hosted via any simple web server (Nginx, S3, etc.) without a live database or API.

## 2. Project Structure
```text
app/
  __init__.py           # Package initialization
  main.py               # Orchestrator: manages queues and the main loop
  config.py             # YAML loader and validation logic
  scheduler.py          # Job producer: tracks timing and pushes to job_queue
  runner.py             # Worker logic: performs HTTP checks and classifies results
  storage.py            # Single-writer: pulls from results_queue to CSV
  cert_storage.py       # SSL certificate metadata storage with TTL caching
  aggregator.py         # Logic: forward-fills buckets and determines UP/DOWN states
  renderer.py           # Output: Jinja2 templates to static HTML
  models.py             # Shared Pydantic/Dataclass schemas
  discover.py           # CLI tool to probe domains and detect bot protection patterns
  storage_backends.py   # Pluggable storage backends (filesystem, R2, S3)
config/                 # Configuration directory
  domains.yaml.example  # Example configuration file
templates/
  base.html             # Shared layout
  index.html            # Global dashboard
  site.html             # Detailed site view
data/                   # CSV storage (/live and /archive)
  live/                 # Current monitoring data
  archive/              # Archived data by date
  certs/                # SSL certificate metadata (JSON)
public/                 # Final generated HTML output
```

## 3. Core Data Models

### Implementation Note: Pydantic for All Models
All configuration and data models use Pydantic `BaseModel` for consistent validation and serialization:
- **`models.py`**: `DomainConfig`, `Result`, `ExpectConfig` - Core data models with validation
- **`config.py`**: `DashboardConfig`, `StorageConfig` - Settings models with field descriptions

Benefits:
- **Automatic validation** - `Field(ge=, le=, ...)` constraints catch errors at instantiation
- **Type safety** - Runtime type checking with clear error messages
- **Serialization** - Easy model export to dict/JSON when needed
- **Documentation** - Field descriptions serve as inline API docs

Error handling uses `pydantic.ValidationError` for model validation issues. Other errors (missing config file, duplicate IDs) continue to use standard exceptions.

### ConfigLoader Settings
- **rotation_interval_seconds**: How long to keep data in `data/live/` before archiving (default: 86400 = 24 hours). Shorter intervals available for high-volume scenarios.
- **retention_days**: How many days to keep archived data before deletion (default: 7). **Note**: Archives are stored indefinitely in `data/archive/`; retention only affects cleanup.

### DomainConfig
- **id**: Unique identifier.
- **url**: The endpoint to check.
- **interval_seconds**: Fixed at 60 seconds (1 minute) for timeline consistency.
- **expect**: Optional. Defaults to `{http_status: 200}`.
  - **http_status**: Expected HTTP status code (default: 200).
  - **body_contains**: Optional string to validate in response body.

### Result
- **timestamp**: ISO UTC string.
- **site_id**: Group identifier.
- **domain_id**: Individual monitor ID.
- **domain_status**: `UP`, `DOWN`, `PROTECTED`, `TIMEOUT`, or `UNKNOWN`.
  - `PROTECTED`: Bot protection detected (DDoS-Guard, Cloudflare, etc.) - counts as UP for site health. Content checks are skipped for protected sites.
- **http_status**: Integer (or null on timeout).
- **latency_ms**: Integer (or null on failure).
- **failure_type**: Structured error classification (FailureType enum): `timeout`, `connection_refused`, `dns_failure`, `ssl_error`, `http_error`, `content_mismatch`, `unexpected_redirect`, `latency_high`, `unknown`.

Note: SSL certificate expiry is stored separately in `data/certs/` (see Section 13), not in the Result model.

## 4. Concurrency & Flow (The Queue Pattern)
To ensure thread safety and prevent CSV file corruption:
1. **Job Queue**: The **Scheduler** pushes `DomainConfig` objects here when they are due.
2. **Worker Pool**: 5–10 concurrent threads pull from the **Job Queue**, execute `runner.py`, and push a completed `Result` to the **Results Queue**.
3. **Single Writer**: The **Main Loop** (Main Thread) is the only component that pulls from the **Results Queue** and passes data to `storage.py`.

## 5. Runner Logic (runner.py)

### HTTP Client Configuration
- **Connection Pooling**: Uses `requests.Session` with `HTTPAdapter` for connection reuse
  - `pool_connections=10`: Number of connection pools to cache
  - `pool_maxsize=20`: Max connections to save in each pool
  - Reduces connection overhead for high-frequency monitoring

The runner classifies results using this priority order:

1. **TIMEOUT**: If request duration exceeds `timeout` (default 20s).

2. **Bot Protection Detection**:
   - Log format: `[PROTECTION] {domain}: type='{protection_type}', detected='{indicator}', status_code={code}`
   - Protection types detected: **DDoS-Guard**, **Cloudflare**, **AWS WAF**, **reCAPTCHA**, **hCaptcha**
   - Detection based on: `Server` header, response headers (e.g., `cf-ray`), and content patterns
   - **All bot protection returns `PROTECTED` (green)** - no signature matching needed
   - Content checks are **skipped** when protection is detected (protection pages don't contain site content)

3. **Content Check** (only when no bot protection detected and `body_contains` configured):
   - Log format: `[CONTENT] {domain}: looking_for='{string}', found={bool}, content_length={len}`
   - If `expect.body_contains` found in response → `UP` (green)
   - If not found → `DOWN` (red) - indicates site may be replaced with error page

4. **Status Check** (when no `body_contains` configured and no protection):
   - If `response.status_code` != `expect.http_status` → `DOWN`

5. **Success**: Status matches expectation → `UP` (green)

## 6. Storage & Rotation (storage.py)
- **Path**: `/data/live/{site}/{domain}.log`.
- **Format**: Simple CSV (append-only) with headers: `timestamp`, `site_id`, `domain_id`, `domain_status`, `http_status`, `latency_ms`, `failure_type`.
- **Cert Storage**: SSL certificate metadata is stored separately in `data/certs/{site}/{domain}.json` (see Section 13).
- **Atomic Writes**: Because only the Main Thread handles the `Results Queue`, standard file appends are safe.
- **Rotation**: Files appended from `/live` to `/archive/{YYYY-MM}/` based on `rotation_interval_seconds` setting (default: monthly aggregation with daily append). Archives are organized by month for easier historical analysis.
- **Retention**: Delete archives older than `retention_days` (default: 7 days).

**Note on Site ID Changes**: Changing a domain's site ID (the part before the dot in `site.domain`) creates a new log directory structure. The old directory remains as an orphaned file. This is intentional - we never auto-delete live log data to prevent accidental data loss. To clean up orphaned directories after changing site IDs:
```bash
rm -rf data/live/old_site_name/
```

**Configuration Precedence** (highest to lowest):
- **rotation_interval**: 1. Constructor param → 2. Config `settings.rotation_interval_seconds` → 3. Default (86400s = 24 hours = daily)
- **retention_days**: 1. Constructor param → 2. Config `settings.retention_days` → 3. Default (7 days)

## 7. Aggregator & State (aggregator.py)

### Bucket Aggregation Rule
Checks occur every 60 seconds (1-minute intervals), but are aggregated into **5-minute buckets**:
- Each 5-minute window collects up to 5 check results
- **0 failures**: UP (green) - all checks passed
- **1 failure**: DEGRADED (yellow) - transient issue detected
- **2+ failures**: DOWN (red) - persistent problem
- **No data**: UNKNOWN (gray)
- Current status uses most recent non-UNKNOWN bucket to avoid interim UNKNOWN display

### Domain Status vs Site Health

**Domain Status** (per URL/check):
- `UP`, `DOWN`, `DEGRADED`, `PROTECTED`, `TIMEOUT`, `UNKNOWN`
- Set by the Runner after each HTTP check
- `UNKNOWN` means no check data exists for this domain (gap in timeline)

**Site Health** (aggregate across all domains in a site):
- `UP`, `DEGRADED`, `DOWN`, `UNKNOWN`
- Computed by the Aggregator from domain statuses
- `UNKNOWN` means **all** domains have no data (brand new site)

### Site Health Computation Rules
1. **Any domain DOWN/TIMEOUT** → Site DOWN
2. **Any domain DEGRADED** → Site DEGRADED
3. **Mix of UNKNOWN + UP/PROTECTED** → Site DOWN (partial data indicates problem)
4. **All domains UP/PROTECTED** → Site UP
5. **All domains UNKNOWN** → Site UNKNOWN (no data at all)

Failures for bucket aggregation: DOWN, TIMEOUT, UNKNOWN
Successes for bucket aggregation: UP, PROTECTED

## 8. Renderer (renderer.py)
- **Throttle**: The renderer should only trigger if `new_data` is present AND at least 30 seconds have passed since the last build.
- **Timeline Rendering**: Display 48 spans (4 hours) of 5-minute buckets:
  - Aggregator produces 5-minute buckets directly
  - Color-coded status spans: `<span class="up">` (Green), `<span class="down">` (Red), `<span class="degraded">` (Yellow), `<span class="protected">` (Green), `<span class="unknown">` (Gray)
- **Timeline Tooltips**: Each timeline span shows detailed information on hover:
  - Timestamp (local time with AM/PM)
    - *Implementation*: Server emits UTC ISO 8601; browser converts to local time via `Date.toLocaleTimeString()`
    - *Rationale*: Supports global viewers without requiring server timezone configuration
  - Status (UP/DOWN/PROTECTED/TIMEOUT/UNKNOWN)
  - HTTP status code (when applicable)
  - Response latency in milliseconds (when applicable)
  - Failure type/reason (e.g., "timeout", "connection error", "unexpected status code")

## 8.1 Dashboard Storage Backends (Optional)

The renderer supports pluggable storage backends for the generated HTML dashboard. By default, files are written to the local filesystem, but R2/S3-compatible object storage can be configured instead of in addition.

### Supported Backends

**1. FilesystemBackend (Default)**
- Writes to local directory (default: `public/`)
- Suitable for development and low-traffic deployments
- Serve with nginx, Caddy, or any static file server

**2. R2Backend (Cloudflare R2)**
- Uploads to Cloudflare R2 bucket
- Zero egress fees make it cost-effective for 5k-30k hits/day (~$0.50/month)
- Global edge caching reduces origin load
- Strongly consistent reads (no stale content issues)

### Configuration

```yaml
storage:
  type: r2  # Options: 'filesystem', 'r2', 's3'
  filesystem:
    output_dir: "public"
  r2:
    account_id: "${FLATMONITOR_R2_ACCOUNT_ID}"
    access_key_id: "${FLATMONITOR_R2_ACCESS_KEY_ID}"
    secret_access_key: "${FLATMONITOR_R2_SECRET_ACCESS_KEY}"
    bucket_name: "flatmonitor-dashboards"
    public_domain: "https://status.yourdomain.com"
```

### Dual Storage Mode (R2/S3 + Local Filesystem)

By default, when using R2 or S3 backends, files are also written to the local filesystem. This provides:
- **Local backup** for debugging or fallback serving
- **Immediate verification** that dashboard generation works
- **Flexibility** to switch between serving methods without regenerating

**Configuration:**
```yaml
storage:
  type: r2
  r2:
    account_id: "${FLATMONITOR_R2_ACCOUNT_ID}"
    bucket_name: "flatmonitor-dashboard"
  filesystem:
    output_dir: "public"
    enabled: true  # Default: true - set to false to skip local files
```

When `filesystem.enabled: true` (default), the system uses `MultiStorageBackend` which:
1. Writes dashboard HTML to both R2/S3 AND local `public/` directory
2. Uploads logs to R2/S3 only (already local for filesystem)
3. Returns R2/S3 URLs for public access

To disable local output when using cloud storage:
```yaml
storage:
  type: r2
  filesystem:
    enabled: false  # Cloud storage only
```

### Implementation Architecture

**StorageBackend Interface:**
```python
class StorageBackend(ABC):
    @abstractmethod
    def write_file(self, relative_path: str, content: str) -> str:
        """Write file and return public URL."""
        pass

    @abstractmethod
    def upload_logs(self, data_dir: Path) -> None:
        """Upload log files from data/live/ and data/archive/ to storage."""
        pass

    @abstractmethod
    def get_log_public_url(self, site_id: str, domain_name: str) -> str:
        """Get public URL for current log file (data/live/)."""
        pass

    @abstractmethod
    def get_archive_log_public_url(self, site_id: str, domain_name: str, date: str) -> str:
        """Get public URL for archived log file (data/archive/{date}/)."""
        pass

    @abstractmethod
    def upload_assets(self, assets_dir: Path) -> None:
        """Upload static assets (images, etc.) from assets_dir to storage."""
        pass
```

**Renderer Integration:**
- Renderer accepts `storage_backend` parameter
- `_build_index()` and `_build_site_page()` use backend instead of direct file writes
- `_get_archive_dates()` discovers available archived months per site using `archive_index.json` (works consistently across filesystem, R2, and S3 backends)
- Site pages include both current log link and dropdown for archived logs (monthly archives)
- Returns public URLs for logging/verification
- **Archive Index**: `data/archive_index.json` tracks available archives for fast discovery without cloud API calls

**Optimizations:**
- **Content deduplication**: Hash-based caching to skip unchanged uploads
- **Short cache headers**: `max-age=60` for real-time feel with R2
- **Connection pooling**: Reuse S3 connections for multiple uploads
- **Asset upload**: Static assets (logo, favicon) in `public/assets/` are automatically uploaded to R2/S3 when using cloud storage backends. Files are uploaded with `max-age=3600` and content-based deduplication.

### Cost Analysis (30k hits/day)

| Metric | R2 | S3-equivalent |
|--------|-----|---------------|
| Class A ops (uploads) | ~86k/month = $0.39 | ~$0.43 |
| Class B ops (reads) | ~900k/month = $0.13 | ~$8.10 |
| Egress | $0 | ~$5-10 |
| **Total** | **~$0.50/month** | **~$15+/month** |

> **Note:** Cost analysis is deployment guidance, not implementation specification. Move to deployment documentation if maintaining separately.

### Deployment Notes

- Environment variables required for R2: `FLATMONITOR_R2_ACCOUNT_ID`, `FLATMONITOR_R2_ACCESS_KEY_ID`, `FLATMONITOR_R2_SECRET_ACCESS_KEY`
- For R2: Set up custom domain or use `*.r2.dev` endpoint
- Local development defaults to filesystem backend
- No web server needed when using R2 (serve directly from bucket)

### Search Engine Indexing Control

The `noindex` setting controls whether search engines should index the dashboard. Useful for staging environments or private instances.

**Configuration:**
```yaml
settings:
  noindex: false  # Default: true (privacy-by-default)
```

**Behavior:**
- When `noindex: true` (default): Adds `<meta name="robots" content="noindex, nofollow">` to all pages
- When `false`: No robots meta tag (search engines may index)

**Use Cases:**
- **Staging**: `staging.example.org` with `noindex: true` — accessible but not discoverable
- **Production**: `status.example.org` without setting — publicly discoverable
Prvate/IternalDfut bhavi()dhoardndexed by rch engins
**Not*bli  StaTus Page re Setlinoindea: fr seeseo allpwtiearch nngtheo n extngnsitive data, add authentication.

### Dashboard Customization

The `dashboard` settings allow customization of the dashboard title, description, logo, favicon, announcements, footer links, and display ordering.

**Configuration:**
```yaml
settings:
  dashboard:
    title: "Service Status"                    # Page title (default: "FlatMonitor")
    header_text: "Service monitoring dashboard"  # Optional subtitle/description
    announcement: "Maintenance scheduled for Saturday"  # Optional banner (plain text only)
    footer_links:                                # Optional links array
      - text: "Terms of Service"
        url: "https://example.org/terms"
    sort_by: "yaml_order"                      # Display order: 'yaml_order' (default), 'severity', or 'alphabetical'
    favicon: "favicon.ico"                      # Optional favicon filename (place in public/assets/)
    logo: "logo.png"                           # Optional header logo image (place in public/assets/)
    header_hint: "Click any site title for detailed status and logs."  # Hint shown above site grid (index page only)
    footer_explanation: "<strong>Custom:</strong> Your explanation here."  # Custom HTML footer text (both pages, optional)
    instance_label: "US-East Primary"          # Optional label for this instance (shows in footer, useful for multi-instance setups)
```

**Behavior:**
- `title`: Used in `<title>` tag, page header, and footer
- `header_text`: Optional subtitle shown below the main title (defaults to "Real-time HTTP monitoring status")
- `announcement`: If set, displays a yellow banner at the top of all pages (plain text only, no HTML)
- `footer_links`: Array of link objects rendered in the footer, opens in new tab
- `sort_by`: Controls display order of sites and domains
  - `yaml_order` (default): Preserves strict order from config/domains.yaml
  - `severity`: Sites sorted by health (DOWN → DEGRADED → UP), domains sorted by health severity (ties broken by config order)
  - `alphabetical`: Sites and domains sorted alphabetically by ID
- `favicon`: Optional favicon filename. Place the image file in `public/assets/` directory
- `logo`: Optional header logo image. Place the image file in `public/assets/` directory. If not set, no logo is displayed
- `instance_label`: Optional identifier for this monitoring instance (e.g., "US-East Primary", "EU-West Secondary"). Displayed in the footer to help identify which instance generated the dashboard. Useful when running multiple instances for redundancy or geographic distribution.

**Auto-Reload:** Dashboard settings are automatically reloaded before each dashboard rebuild (every 30+ seconds when new data is available). Changes to `title`, `logo`, `favicon`, `header_text`, `announcement`, `footer_links`, `sort_by`, `header_hint`, `footer_explanation`, and `instance_label` take effect without restarting the application.

**Note:** Domain and storage configuration changes still require a restart. Only dashboard customization settings support hot-reloading.

**Security Note:** 
- Announcements support plain text only. HTML will be escaped by Jinja2 autoescape.
- `footer_explanation` supports HTML (uses `| safe` filter). Use with caution - only set trusted content.

## 9. Main Loop (main.py)

```python
while True:
    # 1. Schedule checks
    scheduler.tick(job_queue)
    
    # 2. Process results (Single-threaded writing)
    new_data = False
    while not results_queue.empty():
        res = results_queue.get()
        storage.append_csv(res)
        new_data = True
    
    # 3. Aggregation & Rendering (Throttled)
    if new_data and time_to_rebuild():
        aggregator.process_recent_data()
        renderer.build_static_site()
        new_data = False
    
    time.sleep(1)
```

## 10. Default Settings
- **Check Interval**: 60s (fixed).
- **HTTP Timeout**: 20s.
- **History Window**: 4 hours.
- **Bucket Size**: 5 minutes (aggregates up to 5 checks per bucket).
- **Worker Pool Size**: 10 threads.
- **Rotation Interval**: 86400s (24 hours, daily rotation). Configurable for different retention needs.

## 11. Testing Strategy

### Unit Tests
Test individual components in isolation:
- **models.py**: Pydantic model validation, CSV serialization/deserialization
- **config.py**: YAML loading, domain parsing, validation error handling
- **runner.py**: Mock HTTP responses, status classification logic, timeout handling
- **storage.py**: File operations, rotation logic, CSV format correctness
- **aggregator.py**: Bucket aggregation, forward-fill logic, site health determination
- **renderer.py**: Template rendering, throttling logic, output generation

### Integration Tests
Test component interactions:
- Scheduler + Queue: Verify jobs are queued at correct intervals
- Runner + Storage: Check results are properly written to CSV
- Aggregator + Renderer: Validate dashboard generation from stored data

### End-to-End Tests
Full system verification using mock HTTP server:
- Start monitoring with test endpoints
- Verify CSV logs are created with correct format
- Check dashboard HTML is generated and contains expected data
- Test rotation by simulating time passage

### Test Data
Use httpbin.org endpoints for predictable responses:
- `/status/200` - UP state
- `/status/500` - DOWN state
- `/delay/30` - TIMEOUT state
- `/html` with specific content - body_contains validation

### Configuration Example
```yaml
settings:
  rotation_interval_seconds: 86400  # Optional, default 24 hours (daily)

domains:
  - id: site.domain1
    url: https://example.com

  - id: site.domain2
    url: https://api.example.com/health
    expect:
      body_contains: "OK"

### Manual Testing Checklist
- [ ] Configuration loads and validates correctly
- [ ] HTTP checks execute at specified intervals
- [ ] All status classifications (UP, DOWN, PROTECTED, TIMEOUT, UNKNOWN) work
- [ ] CSV files append without corruption
- [ ] Dashboard updates within 30 seconds of new data
- [ ] Site health correctly reflects domain status aggregation
- [ ] Hourly rotation archives files properly
- [ ] Cleanup removes old archives
- [ ] Graceful shutdown processes remaining queue items

## 12. UI Design Requirements

### 12.1 Information Density Principle
The dashboard must present all critical information without requiring user interaction:
- **No click-through required** to see status bars or health details
- All site statuses visible at a glance
- Per-domain timelines visible on the main page
- Latest check details visible inline

### 12.2 Dashboard Layout

#### Header Section
- Dashboard title and generation timestamp

#### Site Status Overview (Top Section)
- Grid/list showing each site with its computed health status
- Format: `site_id {health_badge}`
- Sorted by severity: DOWN → DEGRADED → UP
- Examples:
  - `production 🟢 UP`
  - `staging 🟡 DEGRADED`
  - `legacy 🔴 DOWN`

#### Site Details Cards
Each site displayed as a card containing:

**Card Header:**
- Site ID with health badge (UP/DEGRADED/DOWN)

**Site Summary Bar:**
- Detailed domain status breakdown showing counts per status type
- Format: "N domains" followed by status counts (only showing non-zero statuses)
- Status indicators with semantic colors:
  - **UP** (green) - domains with UP status
  - **PROTECTED** (green) - domains with bot protection active
  - **DOWN** (red) - domains that are down
  - **DEGRADED** (yellow) - domains with single failure in bucket
  - **TIMEOUT** (red) - domains that timed out
  - **UNKNOWN** (gray) - domains with no recent data
- Example: "5 domains 2 UP 1 PROTECTED 1 DOWN 1 TIMEOUT"
- Relative timestamp: "Last check: 2m ago"

**Per-Domain Timeline (Visible Without Clicking):**
- 48 spans (4 hours of 5-minute buckets) per domain
- Compact horizontal bar showing history
- Color-coded: up (green), down (red), protected (green), unknown (gray)

**Per-Domain Details Table:**
Columns: Domain | Status | Response | Cert Expiry
- **Domain**: URL (clickable unless link_disabled is set)
- **Status**: Status badge + latency (e.g., "🟢 UP 45ms")
- **Response**: HTTP code + body_contains check result (✓ or ✗)
- **Cert Expiry**: Days until expiration with warning indicator

### 12.2.1 Site Detail Page - Expected vs Actual Panels

Each domain on the site detail page displays side-by-side comparison panels showing:

**Expected Configuration Panel (Left):**
- Expected HTTP status code (if configured)
- Expected body content string (if configured)
- Expected bot protection signature (status_code and indicator) if configured
- Displayed with blue header accent

**Actual Results Panel (Right):**
- Actual HTTP status code from last check
- Response latency in milliseconds
- Failure type/reason if check failed
- Certificate expiry date with status indicator
- Relative timestamp of last check (e.g., "2m ago")
- **DEGRADED status explanation**: When status is DEGRADED (1 failure), shows "1 check failed in the last 5-minute window" with the failed check details (time, HTTP status, failure reason) from timeline data
- **DOWN status explanation**: When status is DOWN (2+ failures), shows "2 or more checks failed in the last 5-minute window" with all failed check details listed from timeline data
- Displayed with orange header accent

This comparison helps users quickly identify configuration mismatches and understand why a domain has a particular status.

### 12.3 Data Display Specifications

#### Timeline Rendering
- 48 five-minute buckets (4 hours) - directly aggregated, no consolidation needed
- Each bucket as a 3-4px wide span
- Height: 16-18px
- Gap: 2px between spans
- **Direction**: Chronological left-to-right (oldest on left, newest on right)
- **Aggregation Rule**: 1 failure = DEGRADED (yellow), 2+ failures = DOWN (red)
- **Hover Interaction**: Timeline spans scale vertically and show glow effect on hover
- **Tooltip Content**: Time, status, HTTP code, latency, and failure reason (when applicable)

#### Status Badges
- **UP**: Green (#238636) - Content found or all checks passed
- **PROTECTED**: Green (#238636) - Bot protection detected (counts as UP)
- **DOWN/TIMEOUT**: Red (#da3633) - Content missing, wrong status, or error
- **DEGRADED**: Yellow (#f7dc6f) - 1 failure in a 5-minute window (transient issue)
- **UNKNOWN**: Gray (#6e7681) - No recent data (older than 5 minutes)

#### Status Legend
Color legend displayed at the top of each dashboard page:
- **Up / Protected**: Green square - Site is accessible or behind expected bot protection
- **Degraded**: Yellow square - 1 check failed in 5-minute window (transient issue)
- **Down / Timeout**: Red square - 2+ failures, unexpected response, or timeout
- **Unknown**: Gray square - No recent check data

#### Certificate Display
- Valid: "Feb 14 (310d) ✓"
- Expiring soon (≤30d): "Jan 15 (12d) ⚠"
- Expired: "Dec 1 (-45d) ✗ EXPIRED"

#### Direct Links
- Each monitored domain URL must be a clickable link (by default)
- Links open in new tab (`target="_blank" rel="noopener noreferrer"`)
- URL displayed as truncated text (e.g., "api.example.com/v1/health") with full URL on hover
- Links styled distinctly from plain text (underline on hover, appropriate cursor)
- **Config Option**: `link_disabled: bool` (default: false) - When true, display URL as plain text instead of clickable link

### 10. Discovery Tool (discover.py)

The discovery tool probes domains to identify bot protection patterns and generates appropriate configuration.

### Usage
```bash
python -m app.discover https://example.com
python -m app.discover https://example.com --checks 5
python -m app.discover https://example.com --timeout 30
```

The `--checks N` option runs multiple probes to detect **sporadic protection** - some sites serve different responses from rotating servers or CDNs. The tool aggregates indicators across all checks and suggests configuration if any check found protection.

### Detection Logic

The tool analyzes HTTP responses for bot protection indicators:

**Bot Protection Indicators (body):**
- "captcha", "cloudflare", "blocked", "access denied"
- "rate limit", "too many requests", "bot detected"
- "please wait", "checking your browser", "ddos protection"
- "security check", "verify you are human"

**Bot Protection Indicators (headers):**
- `cf-ray` - Cloudflare proxy signature
- `server: cloudflare` - Cloudflare server header
- `x-akamai-request-id` / `akamai-cache-status` - Akamai CDN
- `x-served-by` (contains "fastly") - Fastly CDN
- `x-iinfo` / `incap-ses` - Imperva Incapsula
- `x-amz-cf-id` / `x-amz-cf-pop` - AWS CloudFront
- `x-datadome` / `server: datadome` - DataDome bot protection
- `x-perimeter-x` / `px-captcha` - PerimeterX (HUMAN)

**Bot Protection Status Codes:**
- `503` - Service Unavailable
- `429` - Too Many Requests
- `403` - Forbidden

**CDN Error Codes (NOT bot protection):**
Status codes `520-530` indicate the CDN cannot reach the origin server:
- `522` - Connection timed out (origin unreachable)
- `520` / `521` / `523-530` - Various origin errors

When these codes are detected, the tool reports that the site appears to be down rather than suggesting bot protection configuration.

### Output

**Normal response (no protection):**
```
💡 No bot protection detected - no special configuration needed.
Site appears to be directly accessible (expect status 200).
```

**Bot protection detected:**
```
✅ Bot Protection Detected
   Type: cloudflare
   Status Code: 503

📝 Monitoring Behavior:
   Bot protection sites automatically show PROTECTED (green)
   Content checks are skipped (protection pages don't contain site content)
```

**Sporadic protection detected (--checks N):**
All indicators seen: ['blocked', 'checking your browser']

✅ Bot Protection Detected
   Type: checking your browser
   Status Code: 503

📝 Monitoring Behavior:
   Bot protection sites automatically show PROTECTED (green)
   Content checks are skipped (protection pages don't contain site content)

⚠️  Note: 'blocked' was found in some checks but not this one.
   Protection may be sporadic - this is normal for many sites.

⚠️  Note: 'blocked' was found in some checks but not this one.
   This suggests sporadic protection - configure it to avoid yellow alerts.
```

```
⚠️  CDN origin error detected (status 522).
The site appears to be down - the CDN cannot reach the origin server.
This is not bot protection, it's a server availability issue.
```

### 12.5 Responsive Design
Following the industry-standard pattern for graceful mobile adaptation:

**Desktop (>768px):**
- Full timeline display (48 spans, ~8px each)
- All table columns visible (Domain, Status, Response, Cert Expiry)
- Site summary bar displays horizontally

**Mobile (≤768px):**
- **Timeline**: Compresses naturally using flexbox - 48 spans share available width, each span becomes ~2px or thinner
  - No horizontal scroll required
  - Height reduces slightly (12px vs 16px)
  - Visual pattern remains recognizable
- **Domain Table**: 
  - Font size reduces (0.75rem)
  - Padding tightens
  - Cert Expiry column hidden on very small screens
- **Site Summary**: Wraps to multiple lines if needed, maintains readability

## 13. SSL Certificate Monitoring

### 13.1 Certificate Data Collection
- Extract SSL certificate expiration date during HTTP checks
- Store certificate metadata in separate JSON files (`data/certs/{site}/{domain}.json`)
- Format: ISO 8601 date string (e.g., "2025-02-14T00:00:00Z")
- Null if check failed or non-HTTPS URL
- **Caching**: Certificate checks are cached with 24-hour TTL to avoid TLS overhead on every check

### 13.2 Certificate Storage (cert_storage.py)
The `CertStorage` class manages SSL certificate metadata separately from check results:
- **Location**: `data/certs/{site_id}/{domain_name}.json`
- **TTL**: 24 hours (configurable) - certificates are checked once per day, not every minute
- **Format**: JSON with `cert_expiry`, `last_check`, and metadata
- **Benefits**: 
  - Eliminates TLS handshake overhead on every HTTP check
  - Removes duplicate timestamps from CSV logs
  - Simpler cert status queries without parsing entire log history

### 13.3 Certificate Status Logic
- **Valid**: Expiry > 7 days from now
- **Expiring Soon**: Expiry ≤ 7 days from now
- **Expired**: Expiry < current date

### 13.4 Certificate Display Rules
- Certificate expiry does NOT affect UP/DOWN status
- **Detail page**: Always displays certificate info (shows "N/A" for non-HTTPS)
- **Main page**: Shows certificate warning indicator (⚠) only when ≤7 days to expiry or expired
- Visual indicator with days remaining:
  - Valid (>7 days): "Feb 14 (45d) ✓" (green checkmark)
  - Expiring soon (≤7 days): "Jan 15 (5d) ⚠" (yellow warning)
  - Expired: "Dec 1 (-10d) ✗ EXPIRED" (red X)
- Dashboard reads from `CertStorage`, not CSV logs
