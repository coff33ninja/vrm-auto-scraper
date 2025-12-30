"""Property-based tests for rate limiting."""
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from hypothesis import given, settings, strategies as st

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sources.base import RateLimitedClient


class TestRateLimitingEnforcement:
    """
    Feature: vrm-auto-scraper, Property 14: Rate Limiting Enforcement
    Validates: Requirements 6.2
    
    For any sequence of N API requests to the same source, the total elapsed time
    SHALL be at least (N-1) * rate_limit_delay seconds.
    """
    
    @given(
        num_requests=st.integers(min_value=2, max_value=5),
        rate_limit_delay=st.floats(min_value=0.05, max_value=0.2)
    )
    @settings(max_examples=50, deadline=None)
    def test_rate_limiting_enforcement(self, num_requests: int, rate_limit_delay: float):
        """Property 14: Minimum delay between requests is enforced."""
        client = RateLimitedClient(rate_limit_delay=rate_limit_delay)
        
        # Mock the session to avoid actual HTTP requests
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        
        with patch.object(client.session, 'request', return_value=mock_response):
            start_time = time.time()
            
            for _ in range(num_requests):
                client.get("https://example.com/api")
            
            elapsed_time = time.time() - start_time
        
        # Total time should be at least (N-1) * rate_limit_delay
        # We use a small tolerance for timing precision
        expected_min_time = (num_requests - 1) * rate_limit_delay
        tolerance = 0.05  # 50ms tolerance for timing precision
        
        assert elapsed_time >= expected_min_time - tolerance, \
            f"Expected at least {expected_min_time}s, got {elapsed_time}s"
    
    def test_first_request_no_delay(self):
        """First request should not have any delay."""
        client = RateLimitedClient(rate_limit_delay=1.0)
        
        mock_response = Mock()
        mock_response.status_code = 200
        
        with patch.object(client.session, 'request', return_value=mock_response):
            start_time = time.time()
            client.get("https://example.com/api")
            elapsed_time = time.time() - start_time
        
        # First request should be nearly instant
        assert elapsed_time < 0.1
    
    def test_rate_limit_between_requests(self):
        """Verify delay is enforced between consecutive requests."""
        rate_limit_delay = 0.1
        client = RateLimitedClient(rate_limit_delay=rate_limit_delay)
        
        mock_response = Mock()
        mock_response.status_code = 200
        
        with patch.object(client.session, 'request', return_value=mock_response):
            # First request
            client.get("https://example.com/api")
            first_time = client.get_last_request_time()
            
            # Second request
            client.get("https://example.com/api")
            second_time = client.get_last_request_time()
        
        # Time between requests should be at least rate_limit_delay
        time_diff = second_time - first_time
        assert time_diff >= rate_limit_delay - 0.01  # Small tolerance
