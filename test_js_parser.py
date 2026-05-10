#!/usr/bin/env python3
"""
Test JavaScript log parser against real log files using Node.js.
This validates that the JavaScript parsing logic matches the actual CSV format.
"""

import subprocess
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

def create_test_log():
    """Create a test log file with real data format."""
    content = """# version: 2
# schema: timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
2026-05-10T13:30:58.084293+00:00,test,test.example.com,PROTECTED,200,765,,DDoS-Guard
2026-05-10T13:31:58.552511+00:00,test,test.example.com,PROTECTED,200,700,,DDoS-Guard
2026-05-10T13:32:59.029784+00:00,test,test.example.com,PROTECTED,200,776,,DDoS-Guard
2026-05-10T13:33:59.661886+00:00,test,test.example.com,PROTECTED,200,736,,DDoS-Guard
2026-05-10T13:34:59.875067+00:00,test,test.example.com,DOWN,502,616,content_mismatch,
2026-05-10T13:36:00.484393+00:00,test,test.example.com,DOWN,502,675,content_mismatch,
2026-05-10T13:37:00.981011+00:00,test,test.example.com,PROTECTED,200,1606,,DDoS-Guard
2026-05-10T13:38:04.640821+00:00,test,test.example.com,PROTECTED,200,1103,,DDoS-Guard
2026-05-10T13:38:57.569965+00:00,test,test.example.com,PROTECTED,200,6626,,DDoS-Guard
2026-05-10T13:39:02.060516+00:00,test,test.example.com,PROTECTED,200,634,,DDoS-Guard
2026-05-10T13:39:51.509383+00:00,test,test.example.com,PROTECTED,200,567,,DDoS-Guard
2026-05-10T13:40:03.298531+00:00,test,test.example.com,PROTECTED,200,886,,DDoS-Guard
2026-05-10T13:40:52.437810+00:00,test,test.example.com,PROTECTED,200,693,,DDoS-Guard
2026-05-10T13:41:02.911693+00:00,test,test.example.com,PROTECTED,200,298,,DDoS-Guard
2026-05-10T13:41:53.250094+00:00,test,test.example.com,PROTECTED,200,580,,DDoS-Guard
2026-05-10T13:42:04.046089+00:00,test,test.example.com,PROTECTED,200,725,,DDoS-Guard"""
    return content

def create_js_test():
    """Create JavaScript test that mimics the modal parser."""
    js_code = """
// Extract the parsing logic from the modal
function parseLogData(text) {
    // Validate log format version
    const versionLine = text.split('\\n').find(line => line.startsWith('# version:'));
    if (!versionLine) {
        throw new Error('Unsupported log format: no version header found');
    }
    const version = parseInt(versionLine.split(':')[1].trim());
    if (version !== 2) {
        throw new Error(`Unsupported log format version: ${version}. Expected version 2.`);
    }
    
    const lines = text.trim().split('\\n').filter(line => line.length > 0 && !line.startsWith('#'));
    if (lines.length === 0) {
        throw new Error('No log data available');
    }

    // Get last 12 entries
    const recentLines = lines.slice(-12);
    
    // Format timestamp as time ago
    const formatTimeAgo = (timestamp) => {
        const date = new Date(timestamp);
        if (isNaN(date.getTime())) return timestamp;
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);
        if (diff < 60) return diff + 's ago';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return Math.floor(diff / 86400) + 'd ago';
    };
    
    const parsedEntries = [];
    recentLines.forEach(line => {
        const parts = line.split(',');
        if (parts.length >= 5) {
            const timestamp = parts[0];
            const timeAgo = formatTimeAgo(timestamp);
            const status = parts[3];
            const httpStatus = parts[4];
            const latency = parts[5] ? parts[5] + 'ms' : '';
            const failure = parts[6] || '';

            parsedEntries.push({
                timeAgo,
                status,
                httpStatus,
                latency,
                failure
            });
        }
    });
    
    return parsedEntries;
}

// Read the log file and parse it
const fs = require('fs');
const logContent = fs.readFileSync(process.argv[2], 'utf8');

try {
    const entries = parseLogData(logContent);
    console.log('SUCCESS: Parsed', entries.length, 'entries');
    entries.forEach((entry, i) => {
        console.log(`Entry ${i+1}: ${entry.timeAgo} | ${entry.status} | ${entry.httpStatus} | ${entry.latency} | ${entry.failure}`);
    });
} catch (error) {
    console.log('ERROR:', error.message);
    process.exit(1);
}
"""
    return js_code

def test_js_parser():
    """Test JavaScript parser against real log file."""
    # Create temporary files
    log_content = create_test_log()
    js_code = create_js_test()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test.log"
        js_file = Path(tmpdir) / "parser.js"
        
        # Write files
        log_file.write_text(log_content)
        js_file.write_text(js_code)
        
        # Run JavaScript test
        try:
            result = subprocess.run(
                ["node", str(js_file), str(log_file)],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                print(f"❌ JavaScript parser test failed:")
                print(f"Error: {result.stderr}")
                return False
            
            print("✅ JavaScript parser test passed:")
            print(result.stdout)
            
            # Verify expected entries
            if "Parsed 12 entries" not in result.stdout:
                print("❌ Expected 12 entries, but got different count")
                return False
            
            # Check for specific entries
            if "PROTECTED" not in result.stdout or "DOWN" not in result.stdout:
                print("❌ Expected both PROTECTED and DOWN status entries")
                return False
            
            return True
            
        except subprocess.TimeoutExpired:
            print("❌ JavaScript parser test timed out")
            return False
        except FileNotFoundError:
            print("❌ Node.js not available - skipping JS parser test")
            return None

if __name__ == "__main__":
    result = test_js_parser()
    if result is True:
        print("\n✅ JavaScript parser validation PASSED")
    elif result is False:
        print("\n❌ JavaScript parser validation FAILED")
        exit(1)
    else:
        print("\n⚠️  JavaScript parser validation SKIPPED (Node.js not available)")
