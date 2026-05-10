import sys
sys.path.append('app')
from app.renderer import Renderer
from datetime import datetime, timezone
from app.aggregator import DomainStatus, SiteHealth, Bucket
from app.storage_backends import FilesystemBackend

renderer = Renderer(
    templates_dir='templates',
    output_dir='/tmp/test_modal',
    storage_backend=FilesystemBackend('/tmp/test_modal')
)

now = datetime.now(timezone.utc)
data = {
    'sites': {
        'test': {
            'name': 'Test Site',
            'domain_count': 1,
            'up_count': 1,
            'down_count': 0,
            'timeout_count': 0,
            'unknown_count': 0,
            'health': SiteHealth.UP,
            'domains': {
                'example.com': {
                    'status': DomainStatus.UP,
                    'url': 'https://example.com',
                    'link_disabled': False,
                    'last_check': {
                        'timestamp': now.isoformat(),
                        'http_status': 200,
                        'latency_ms': 100
                    }
                }
            },
            'buckets': {'example.com': [Bucket(now, DomainStatus.UP)]},
            'bucket_count': 48,
            'last_check': now.isoformat()
        }
    },
    'generated_at': now.isoformat()
}

renderer.build_static_site(data)

with open('/tmp/test_modal/test.html', 'r') as f:
    content = f.read()

import re
matches = re.findall(r"data-archive-links='([^']*)'", content)
print('Found', len(matches), 'single-quoted attributes')
if matches:
    print('First attribute value:', matches[0][:80])
    try:
        import json
        parsed = json.loads(matches[0])
        print('Successfully parsed as JSON:', type(parsed))
    except Exception as e:
        print('JSON parse error:', e)
