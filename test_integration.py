#!/usr/bin/env python3
"""
Full integration test for the log modal pipeline.
Tests: build_static_site() -> HTML generation -> HTTP fetch -> JavaScript parsing
"""

import subprocess
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone

def create_test_data():
    """Create test data with real log files."""
    now = datetime.now(timezone.utc)
    return {
        'sites': {
            'test': {
                'name': 'Test Site',
                'domain_count': 1,
                'up_count': 1,
                'down_count': 0,
                'timeout_count': 0,
                'unknown_count': 0,
                'health': 'UP',
                'domains': {
                    'example.com': {
                        'status': 'UP',
                        'url': 'https://example.com',
                        'link_disabled': False,
                        'last_check': {
                            'timestamp': now.isoformat(),
                            'http_status': 200,
                            'latency_ms': 100
                        }
                    }
                },
                'buckets': {'example.com': [{'timestamp': now, 'status': 'UP'}]},
                'bucket_count': 48,
                'last_check': now.isoformat()
            }
        },
        'generated_at': now.isoformat()
    }

def create_log_file(log_dir: Path):
    """Create a real log file with version 2 format."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "example.com.log"
    
    content = """# version: 2
# schema: timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
2026-05-10T13:30:58.084293+00:00,test,example.com,PROTECTED,200,765,,DDoS-Guard
2026-05-10T13:31:58.552511+00:00,test,example.com,PROTECTED,200,700,,DDoS-Guard
2026-05-10T13:32:59.029784+00:00,test,example.com,DOWN,502,616,content_mismatch,
2026-05-10T13:33:59.661886+00:00,test,example.com,PROTECTED,200,776,,DDoS-Guard
2026-05-10T13:34:59.875067+00:00,test,example.com,PROTECTED,200,736,,DDoS-Guard
2026-05-10T13:35:00.484393+00:00,test,example.com,PROTECTED,200,1606,,DDoS-Guard
2026-05-10T13:36:04.640821+00:00,test,example.com,PROTECTED,200,1103,,DDoS-Guard
2026-05-10T13:37:57.569965+00:00,test,example.com,PROTECTED,200,6626,,DDoS-Guard
2026-05-10T13:38:02.060516+00:00,test,example.com,PROTECTED,200,634,,DDoS-Guard
2026-05-10T13:39:51.509383+00:00,test,example.com,PROTECTED,200,567,,DDoS-Guard
2026-05-10T13:40:03.298531+00:00,test,example.com,PROTECTED,200,886,,DDoS-Guard
2026-05-10T13:41:52.437810+00:00,test,example.com,PROTECTED,200,693,,DDoS-Guard
2026-05-10T13:42:04.046089+00:00,test,example.com,PROTECTED,200,298,,DDoS-Guard
"""
    log_file.write_text(content)
    return log_file

def test_full_pipeline():
    """Test the complete pipeline: build -> serve -> fetch -> parse."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        public_dir = tmpdir / "public"
        data_dir = tmpdir / "data"
        
        # Create test log file
        log_file = create_log_file(data_dir / "live" / "test")
        
        # Just copy logs using FilesystemBackend directly
        print("🔨 Copying logs...")
        copy_script = f"""
import sys
sys.path.append('app')
from app.storage_backends import FilesystemBackend
from pathlib import Path

backend = FilesystemBackend('{public_dir}')
result = backend.upload_logs(Path('{data_dir}'))
print(f"Copied {{result['uploaded']}} log files")
"""
        
        result = subprocess.run(
            ["python", "-c", copy_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            print(f"❌ Copy failed: {result.stderr}")
            return False
        
        # Check logs were copied
        public_log = public_dir / "logs" / "test" / "example.com.log"
        if not public_log.exists():
            print("❌ Log file not copied to public/logs/")
            return False
        
        print("✅ Site built and logs copied")
        
        # Start HTTP server
        print("🌐 Starting HTTP server...")
        server_proc = subprocess.Popen(
            ["python", "-m", "http.server", "8081"],
            cwd=str(public_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        try:
            # Wait for server to start
            time.sleep(2)
            
            # Check if server is running
            if server_proc.poll() is not None:
                stdout, stderr = server_proc.communicate()
                print(f"❌ Server failed to start: {stderr.decode()}")
                return False
            
            # Test fetch with Node.js
            print("📡 Testing log fetch and parsing...")
            fetch_test = f"""
const http = require('http');
const fs = require('fs');

// Fetch the log file
http.get('http://127.0.0.1:8081/logs/test/example.com.log', (res) => {{
    let data = '';
    res.on('data', (chunk) => data += chunk);
    res.on('end', () => {{
        // Parse the log data (same as modal)
        try {{
            const versionLine = data.split('\\n').find(line => line.startsWith('# version:'));
            if (!versionLine) {{
                throw new Error('Unsupported log format: no version header found');
            }}
            const version = parseInt(versionLine.split(':')[1].trim());
            if (version !== 2) {{
                throw new Error(`Unsupported log format version: ${{version}}. Expected version 2.`);
            }}
            
            const lines = data.trim().split('\\n').filter(line => line.length > 0 && !line.startsWith('#'));
            const dataLines = lines.slice(1);
            const recentLines = dataLines.slice(-12);
            
            const entries = [];
            recentLines.forEach(line => {{
                const parts = line.split(',');
                if (parts.length >= 5) {{
                    const timestamp = parts[0];
                    const status = parts[3];
                    const httpStatus = parts[4];
                    const latency = parts[5] ? parts[5] + 'ms' : '';
                    const failure = parts[6] || '';
                    
                    entries.push({{
                        timestamp,
                        status,
                        httpStatus,
                        latency,
                        failure
                    }});
                }}
            }});
            
            console.log(JSON.stringify({{
                success: true,
                entries: entries.length,
                protected: entries.filter(e => e.status === 'PROTECTED').length,
                down: entries.filter(e => e.status === 'DOWN').length
            }}));
        }} catch (error) {{
            console.log(JSON.stringify({{
                success: false,
                error: error.message
            }}));
            process.exit(1);
        }}
    }});
}}).on('error', (err) => {{
    console.log(JSON.stringify({{
        success: false,
        error: err.message
    }}));
    process.exit(1);
}});
"""
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                f.write(fetch_test)
                js_file = f.name
            
            try:
                result = subprocess.run(
                    ["node", js_file],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode != 0:
                    print(f"❌ Fetch/parse failed: {result.stderr}")
                    print(f"   stdout: {result.stdout}")
                    return False
                
                import json
                result_data = json.loads(result.stdout)
                
                if not result_data["success"]:
                    print(f"❌ Parse error: {result_data['error']}")
                    return False
                
                print(f"✅ Successfully parsed {result_data['entries']} entries")
                print(f"   - {result_data['protected']} PROTECTED")
                print(f"   - {result_data['down']} DOWN")
                
                return True
                
            finally:
                Path(js_file).unlink(missing_ok=True)
                
        finally:
            server_proc.terminate()
            server_proc.wait(timeout=5)
    
    return False

if __name__ == "__main__":
    if test_full_pipeline():
        print("\n✅ Full integration test PASSED")
    else:
        print("\n❌ Full integration test FAILED")
        exit(1)
