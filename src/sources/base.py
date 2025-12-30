"""Base classes and interfaces for model sources."""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import requests


@dataclass
class ModelInfo:
    """Information about a model from a source."""
    source_model_id: str
    name: str
    artist: str
    source_url: str
    is_downloadable: bool
    license: Optional[str] = None
    license_url: Optional[str] = None
    download_url: Optional[str] = None


class BaseSource(ABC):
    """Abstract base class for model sources."""
    
    @abstractmethod
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        """
        Enumerate downloadable models from this source.
        
        Args:
            keywords: Search keywords to filter models
            max_results: Maximum number of results to return
            
        Yields:
            ModelInfo objects for each found model
        """
        pass
    
    @abstractmethod
    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        """
        Download a model file.
        
        Args:
            model: ModelInfo with download information
            output_dir: Directory to save the downloaded file
            
        Returns:
            Path to the downloaded file
        """
        pass
    
    @abstractmethod
    def get_source_name(self) -> str:
        """Return the source identifier string."""
        pass


class RateLimitedClient:
    """HTTP client with rate limiting and retry logic."""
    
    def __init__(
        self,
        rate_limit_delay: float = 1.0,
        max_retries: int = 3,
        base_backoff: float = 1.0,
    ):
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self._last_request_time: float = 0
        self.session = requests.Session()
    
    def _wait_for_rate_limit(self) -> None:
        """Wait if needed to respect rate limit."""
        if self._last_request_time > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.rate_limit_delay:
                time.sleep(self.rate_limit_delay - elapsed)
    
    def request(
        self,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        stream: bool = False,
    ) -> requests.Response:
        """
        Make an HTTP request with rate limiting and retry logic.
        
        Retries on 429 (rate limited) and 5xx errors with exponential backoff.
        """
        self._wait_for_rate_limit()
        
        last_error: Optional[Exception] = None
        
        for attempt in range(self.max_retries + 1):
            try:
                self._last_request_time = time.time()
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json,
                    stream=stream,
                    timeout=30,
                )
                
                # Check for rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        wait_time = float(retry_after)
                    else:
                        wait_time = self.base_backoff * (2 ** attempt)
                    time.sleep(wait_time)
                    continue
                
                # Check for server errors
                if response.status_code >= 500:
                    wait_time = self.base_backoff * (2 ** attempt)
                    time.sleep(wait_time)
                    continue
                
                return response
                
            except requests.RequestException as e:
                last_error = e
                if attempt < self.max_retries:
                    wait_time = self.base_backoff * (2 ** attempt)
                    time.sleep(wait_time)
                    continue
                raise
        
        # If we get here, all retries failed
        if last_error:
            raise last_error
        raise requests.RequestException(f"Request failed after {self.max_retries} retries")
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """Make a GET request."""
        return self.request("GET", url, **kwargs)
    
    def post(self, url: str, **kwargs) -> requests.Response:
        """Make a POST request."""
        return self.request("POST", url, **kwargs)
    
    def download_file(self, url: str, output_path: Path, headers: Optional[dict] = None) -> Path:
        """Download a file with streaming."""
        self._wait_for_rate_limit()
        self._last_request_time = time.time()
        
        response = self.session.get(url, headers=headers, stream=True, timeout=300)
        response.raise_for_status()
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return output_path
    
    def get_last_request_time(self) -> float:
        """Get the timestamp of the last request."""
        return self._last_request_time
