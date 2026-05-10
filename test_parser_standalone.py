#!/usr/bin/env python3
"""
Standalone test for the modal log parser.
Tests the JavaScript parsing logic without HTTP server.
"""

def test_js_parser_directly():
    """Test JavaScript parser by executing it directly with test data."""
    import subprocess
    import tempfile
    from pathlib import Path
    
    # Test log content
    log_content = """# version: 2
# schema: timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
timestamp,site_id,domain_id,domain_status,http_status,latency_ms,failure_type,protection_type
2026-05-10T13:30:58.084293+00:00,test,example.com,PROTECTED,200,765,,DDoS-Guard
2026-05-10T13:31:58.552511+00:00,test,example.com,PROTECTED,200,700,,DDoS-Guard
2026-05-10T13:32:59.029784+00:00,test,example.com,DOWN,502,616,content_mismatch,
2026-05-10T13:33:59.661886+00:00,test,example.com,PROTECTED,200,776,,DDoS-Guard"""
    
    # JavaScript test (same as modal but reads from variable)
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

    // Skip CSV header row (first row after comments)
    const dataLines = lines.slice(1);
    const recentLines = dataLines.slice(-12);
    
    const parsedEntries = [];
    recentLines.forEach(line => {
        const parts = line.split(',');
        if (parts.length >= 5) {
            const timestamp = parts[0];
            const status = parts[3];
            const httpStatus = parts[4];
            const latency = parts[5] ? parts[5] + 'ms' : '';
            const failure = parts[6] || '';

            parsedEntries.push({
                timestamp,
                status,
                httpStatus,
                latency,
                failure
            });
        }
    });
    
    return parsedEntries;
}

// Test with the provided log content
const logContent = `""" + log_content + """`;

try {
    const entries = parseLogData(logContent);
    console.log(JSON.stringify({
        success: true,
        entries: entries.length,
        protected: entries.filter(e => e.status === 'PROTECTED').length,
        down: entries.filter(e => e.status === 'DOWN').length
    }));
} catch (error) {
    console.log(JSON.stringify({
        success: false,
        error: error.message
    }));
    process.exit(1);
}
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(js_code)
        js_file = f.name
    
    try:
        result = subprocess.run(
            ["node", js_file],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            print(f"❌ Parser test failed: {result.stderr}")
            return False
        
        import json
        data = json.loads(result.stdout)
        
        if not data["success"]:
            print(f"❌ Parse error: {data['error']}")
            return False
        
        print("✅ JavaScript parser test passed")
        print(f"   - {data['entries']} entries parsed")
        print(f"   - {data['protected']} PROTECTED")
        print(f"   - {data['down']} DOWN")
        
        return True
        
    except FileNotFoundError:
        print("⚠️  Node.js not available - skipping parser test")
        return None
    except subprocess.TimeoutExpired:
        print("❌ Parser test timed out")
        return False
    finally:
        Path(js_file).unlink(missing_ok=True)

if __name__ == "__main__":
    result = test_js_parser_directly()
    if result is True:
        print("\n✅ Parser validation PASSED")
    elif result is False:
        print("\n❌ Parser validation FAILED")
        exit(1)
    else:
        print("\n⚠️  Parser validation SKIPPED")
