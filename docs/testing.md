# Testing Guide

## Test Categories

FlatMonitor uses pytest markers to categorize tests:

### Unit Tests (`@pytest.mark.unit`)
- **Purpose**: Test logic in isolation without external dependencies
- **Backend**: Uses mocks for all external services (R2, S3, HTTP requests)
- **Run**: `pytest -m unit`
- **Speed**: Fast, no network calls required

### Integration Tests (`@pytest.mark.integration`)
- **Purpose**: Test component interactions and full pipeline
- **Backend**: Uses real filesystem, mock HTTP services (httpbin.org)
- **Run**: `pytest -m integration`
- **Speed**: Medium, may make network calls to test services

### Backend Tests (`@pytest.mark.backend`)
- **Purpose**: Test real cloud storage backends (R2, S3)
- **Run**: `pytest -m backend`
- **Speed**: Slow, requires real credentials and network access

### Slow Tests (`@pytest.mark.slow`)
Marked as slow and skipped by default for regular CI/CD:

```bash
# Run all tests (excludes slow by default)
pytest

# Include slow tests
pytest -m slow

# Run only slow integration tests
pytest -m "slow and integration"
```

**`test_full_modal_pipeline`**
- Tests complete flow: build → serve → fetch → parse
- **Fragile**: May fail due to port conflicts, Node.js issues, network timing
- **Purpose**: Validates entire pipeline before releases
- **Not recommended**: For regular CI/CD due to external dependencies

## Test Fragility

### Robust Tests (Low Maintenance)
- ✅ Template rendering tests
- ✅ Version validation tests  
- ✅ Parser logic tests with static data

### Fragile Tests (High Maintenance)
- ❌ Network-dependent tests
- ❌ HTTP server tests
- ❌ Port-binding tests

When fragile tests fail, first check environmental issues:
1. Port availability (8081 for integration tests)
2. Node.js installation and version
3. Network connectivity
4. File permissions

## Running Tests

### Quick Development
```bash
# Run fast unit tests only
pytest -m "not slow"
```

### Full Validation
```bash
# Run all tests including slow integration tests
pytest -m slow
```

### CI/CD Recommendation
Use unit tests for continuous integration. Run slow tests:
- Before releases
- When making changes to the modal pipeline
- For manual validation

For R2 backend testing:
```bash
export R2_ACCOUNT_ID="your-account-id"
export R2_ACCESS_KEY_ID="your-access-key"
export R2_SECRET_ACCESS_KEY="your-secret-key"
export R2_BUCKET_NAME="your-test-bucket"
```

For S3 backend testing:
```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export S3_BUCKET_NAME="your-test-bucket"
export S3_ENDPOINT_URL="https://s3.amazonaws.com"  # optional
```

### Running Tests

#### All tests (recommended for CI):
```bash
pytest
# Unit tests run + integration tests
# Backend tests are skipped without credentials
```

#### Only unit tests (fastest):
```bash
pytest -m unit
```

#### Only integration tests:
```bash
pytest -m integration
```

#### Backend tests (requires credentials):
```bash
pytest -m backend
```

#### Skip slow tests:
```bash
pytest -m "not slow"
```

## Test Structure

### Unit Tests with Mocked Backends

```python
@pytest.mark.unit
@patch('app.storage_backends.R2Backend')
def test_r2_backend_creation(self, mock_r2):
    # Test logic without real API calls
    backend = create_storage_backend(config)
    mock_r2.assert_called_once_with(...)
```

### Integration Tests with Real Services

```python
@pytest.mark.integration
def test_full_pipeline(self):
    # Test component interaction
    # Uses filesystem storage, mock HTTP services
```

### Backend Tests with Real Credentials

```python
@pytest.mark.backend
def test_r2_real_connection(self):
    # Only runs with credentials available
    # Makes real API calls to R2/S3
```

## Modal & Log Display Tests

When the log modal was refactored from inline `onclick` to data attributes, several integration failures occurred that unit tests did not catch:

**Required integration test coverage:**
- Verify `data-archive-links` attribute contains valid JSON after HTML parsing (regression: double quotes inside double-quoted attribute broke parsing)
- Verify `data-log-path` resolves to a file that exists after `build_static_site()` (regression: FilesystemBackend didn't copy logs to public directory)
- Verify generated log file can be parsed by the modal's JavaScript parser (regression: JS assumed space-separated but logs are CSV)

## Best Practices

1. **Use mocks for unit tests** - Never make real API calls in unit tests
2. **Mark tests appropriately** - Use `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.backend`
3. **Test credential validation** - Verify proper error handling for missing credentials
4. **Skip gracefully** - Backend tests should skip with clear messages when credentials are missing
5. **Use test buckets** - Never use production buckets for testing

## CI/CD Integration

In CI environments:
```bash
# Run all tests that don't require credentials
pytest -m "not backend"

# Or run with credentials if available (separate stage)
pytest -m backend
```

This ensures:
- Fast feedback for most changes
- No credential leaks in CI logs
- Backend testing only when appropriate
