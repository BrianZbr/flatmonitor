# Deployment Guide

## Production Setup with systemd

Create `/etc/systemd/system/flatmonitor.service`:

```ini
[Unit]
Description=FlatMonitor - Synthetic Monitoring
After=network.target

[Service]
Type=simple
User=flatmonitor
Group=flatmonitor
WorkingDirectory=/opt/flatmonitor
Environment=PYTHONPATH=/opt/flatmonitor
Environment=FLATMONITOR_CONFIG=/opt/flatmonitor/config/domains.yaml
ExecStart=/opt/flatmonitor/.venv/bin/python -m app.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=flatmonitor

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo useradd -r -s /bin/false flatmonitor
sudo systemctl daemon-reload
sudo systemctl enable flatmonitor
sudo systemctl start flatmonitor
sudo systemctl status flatmonitor
```

## Nginx Configuration

Serve the static dashboard:

```nginx
server {
    listen 80;
    server_name monitor.example.com;
    root /opt/flatmonitor/public;
    index index.html;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    # Cache static assets (optional)
    location ~* \.(html|css|js)$ {
        expires 30s;
        add_header Cache-Control "public, must-revalidate";
    }

    # Redirect 404s to index (SPA behavior)
    error_page 404 /index.html;
}
```

## Docker Compose (Recommended)

Simplest deployment - just edit `config/domains.yaml` and run:

```bash
# 1. Copy example config and edit
cp config/domains.yaml.example config/domains.yaml
# Edit config/domains.yaml with your URLs

# 2. Start monitoring
docker compose up -d

# 3. Serve dashboard (optional Caddy for HTTPS)
# Uncomment caddy service in docker-compose.yml, then:
docker compose up -d caddy
```

The dashboard is generated at `./public/index.html`. Mount this directory to any web server (nginx, Caddy, S3, etc.).

## Manual Docker

If you prefer plain Docker:

```bash
docker build -t flatmonitor .
docker run -d \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/public:/app/public \
  -v $(pwd)/config/domains.yaml:/app/config/domains.yaml:ro \
  --name flatmonitor \
  flatmonitor
```

## Cloud Storage Backends

FlatMonitor supports pluggable storage backends for hosting the dashboard. By default, files are written to the local filesystem, but you can configure R2/S3-compatible object storage for deployment scenarios with higher traffic or when you don't want to run a web server.

### Supported Backends

| Backend | Best For | Cost (30k hits/day) |
|---------|----------|---------------------|
| **Filesystem** (default) | Development, low-traffic, existing web server | Free (use your own server) |
| **R2** (Cloudflare) | 5k-30k hits/day, zero egress fees | ~$0.50/month |
| **S3** (AWS) | Enterprise AWS environments | ~$15+/month |

### Configuration

Add to your `config/domains.yaml`:

```yaml
settings:
  storage:
    type: r2  # Options: 'filesystem', 'r2', 's3'
    r2:
      account_id: "${R2_ACCOUNT_ID}"
      access_key_id: "${R2_ACCESS_KEY_ID}"
      secret_access_key: "${R2_SECRET_ACCESS_KEY}"
      bucket_name: "${R2_BUCKET_NAME}"
      public_domain: "https://status.yourdomain.com"  # Optional
```

### Dual Storage Mode (Cloud + Local)

When using R2 or S3, FlatMonitor also writes files locally by default. This gives you:
- Local backup for debugging or fallback serving
- Immediate verification that dashboard generation works
- Flexibility to switch serving methods without regenerating

```yaml
settings:
  storage:
    type: r2
    r2:
      account_id: "${R2_ACCOUNT_ID}"
      bucket_name: "flatmonitor-dashboard"
    filesystem:
      output_dir: "public"
      enabled: true  # Default: true - set to false for cloud-only
```

To disable local output when using cloud storage:
```yaml
settings:
  storage:
    type: r2
    filesystem:
      enabled: false
```

### Environment Variables

All cloud backends support environment variable substitution using `${VAR_NAME}` syntax:

| Variable | Backend | Description |
|----------|---------|-------------|
| `R2_ACCOUNT_ID` | R2 | Cloudflare account ID (find in dashboard URL) |
| `R2_ACCESS_KEY_ID` | R2 | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | R2 | R2 API token secret key |
| `R2_BUCKET_NAME` | R2 | R2 bucket name |
| `AWS_ACCESS_KEY_ID` | S3 | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | S3 | AWS IAM secret key |
| `S3_BUCKET_NAME` | S3 | S3 bucket name |

### Cloudflare R2 Setup

1. **Create bucket**: Cloudflare Dashboard → R2 → Create bucket
2. **Enable public access**: Bucket settings → Allow public access (or connect custom domain)
3. **Create API token**: R2 → Manage R2 API Tokens → Create Token (Object Read & Write)
4. **Configure**: Set the 4 `R2_*` environment variables

**Cost advantage**: R2 has zero egress fees. At 30k hits/day:
- R2: ~$0.50/month
- S3 equivalent: ~$15+/month (mostly egress fees)

### S3-Compatible Services

For MinIO, Wasabi, or other S3-compatible services:

```yaml
settings:
  storage:
    type: s3
    s3:
      access_key_id: "${AWS_ACCESS_KEY_ID}"
      secret_access_key: "${AWS_SECRET_ACCESS_KEY}"
      bucket_name: "flatmonitor-dashboard"
      endpoint_url: "https://s3.your-provider.com"
      public_domain: "https://status.yourdomain.com"
```

## Backup Strategy

Essential data to backup:
```bash
# Backup script (run via cron)
#!/bin/bash
BACKUP_DIR="/backup/flatmonitor-$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"
cp -r /opt/flatmonitor/config/domains.yaml "$BACKUP_DIR/"
cp -r /opt/flatmonitor/data/archive "$BACKUP_DIR/"
# Optional: tar and upload to S3/rsync
```
