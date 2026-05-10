from html.parser import HTMLParser
import json

class LogButtonParser(HTMLParser):
    """Extract data-archive-links from rendered HTML and validate JSON."""
    
    def __init__(self):
        super().__init__()
        self.archive_links = []
        self.in_log_button = False
        self.current_attr = None
    
    def handle_starttag(self, tag, attrs):
        if tag == "button" and any(attr[0] == "class" and "log-link" in attr[1] for attr in attrs):
            self.in_log_button = True
            # Extract data-archive-links attribute
            for attr_name, attr_value in attrs:
                if attr_name == "data-archive-links":
                    self.archive_links.append(attr_value)
    
    def handle_endtag(self, tag):
        if tag == "button" and self.in_log_button:
            self.in_log_button = False
    
    def validate_json(self):
        """Validate all extracted archive_links are valid JSON arrays."""
        errors = []
        for i, attr_value in enumerate(self.archive_links):
            try:
                parsed = json.loads(attr_value)
                if not isinstance(parsed, list):
                    errors.append(f"Button {i}: Expected JSON array, got {type(parsed)}")
            except json.JSONDecodeError as e:
                errors.append(f"Button {i}: Invalid JSON - {e}")
        return errors

def test_json_in_html_attribute_quoting():
    """Test that data-archive-links attributes contain valid JSON after HTML parsing."""
    
    # Test case 1: Single-quoted attribute (current fix)
    html_good = '<button class="log-link" data-archive-links=\'[{"date": "2026-05", "url": "test.log"}]\'>logs</button>'
    parser = LogButtonParser()
    parser.feed(html_good)
    errors = parser.validate_json()
    assert not errors, f"Single-quoted attribute should work: {errors}"
    
    # Test case 2: Double-quoted attribute (the bug)
    html_bad = '<button class="log-link" data-archive-links="[{"date": "2026-05", "url": "test.log"}]">logs</button>'
    parser = LogButtonParser()
    parser.feed(html_bad)
    errors = parser.validate_json()
    assert errors, "Double-quoted attribute should fail JSON parsing"
    assert "Invalid JSON" in errors[0], f"Should be JSON error, got: {errors}"
    
    # Test case 3: Empty array
    html_empty = '<button class="log-link" data-archive-links=\'[]\'>logs</button>'
    parser = LogButtonParser()
    parser.feed(html_empty)
    errors = parser.validate_json()
    assert not errors, f"Empty array should work: {errors}"
    
    # Test case 4: Multiple buttons
    html_multi = '''
    <button class="log-link" data-archive-links=\'[{"date": "2026-05"}]\'>logs1</button>
    <button class="log-link" data-archive-links=\'[{"date": "2026-04"}]\'>logs2</button>
    '''
    parser = LogButtonParser()
    parser.feed(html_multi)
    assert len(parser.archive_links) == 2, "Should find 2 buttons"
    errors = parser.validate_json()
    assert not errors, f"Multiple buttons should work: {errors}"

if __name__ == "__main__":
    test_json_in_html_attribute_quoting()
    print("✅ All HTML attribute JSON parsing tests passed")
