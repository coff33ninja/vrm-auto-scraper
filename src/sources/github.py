"""GitHub source implementation."""
import re
from pathlib import Path
from typing import Iterator, Optional

from .base import BaseSource, ModelInfo, RateLimitedClient


class GitHubSource(BaseSource):
    """
    GitHub source for downloading VRM models from repositories.
    
    Searches for VRM files in public repositories using GitHub's code search API.
    Optional token for higher rate limits.
    """
    
    API_BASE = "https://api.github.com"
    
    def __init__(self, token: Optional[str] = None, rate_limit_delay: float = 1.0):
        self.token = token
        self.client = RateLimitedClient(rate_limit_delay=rate_limit_delay)
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if token:
            self.headers["Authorization"] = f"token {token}"
    
    def get_source_name(self) -> str:
        return "github"
    
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        """
        Search for VRM files on GitHub.
        
        Uses code search to find .vrm files in repositories.
        """
        # Build search query for VRM files
        search_terms = keywords if keywords else ["vrm", "vroid"]
        query = f"extension:vrm {' '.join(search_terms)}"
        
        url = f"{self.API_BASE}/search/code"
        params = {
            "q": query,
            "per_page": min(max_results, 100),
        }
        
        count = 0
        
        while count < max_results:
            response = self.client.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                # GitHub code search requires authentication
                # Fall back to repository search
                yield from self._search_repositories(keywords, max_results - count)
                break
            
            data = response.json()
            items = data.get("items", [])
            
            for item in items:
                if count >= max_results:
                    break
                
                model = self._parse_code_result(item)
                if model:
                    yield model
                    count += 1
            
            # Check for next page
            if len(items) == 0 or count >= data.get("total_count", 0):
                break
            
            # GitHub pagination via Link header
            link_header = response.headers.get("Link", "")
            next_url = self._parse_next_link(link_header)
            if not next_url:
                break
            
            url = next_url
            params = {}
    
    def _search_repositories(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        """Fall back to repository search for VRM-related repos."""
        count = 0
        
        # Known VRM sample repositories
        known_repos = [
            "vrm-c/UniVRM",
            "pixiv/three-vrm", 
            "vrm-c/vrm-specification",
        ]
        
        # Search known repos first
        for repo_name in known_repos:
            if count >= max_results:
                break
            for model in self._find_vrm_in_repo_recursive(repo_name, max_results - count):
                yield model
                count += 1
                if count >= max_results:
                    break
        
        if count >= max_results:
            return
        
        # Then search for VRM-related repositories
        search_terms = keywords if keywords else ["vrm", "vroid", "avatar"]
        query = " ".join(search_terms) + " vrm sample"
        
        url = f"{self.API_BASE}/search/repositories"
        params = {
            "q": query,
            "per_page": min(max_results, 30),
            "sort": "stars",
        }
        
        response = self.client.get(url, headers=self.headers, params=params)
        
        if response.status_code != 200:
            return
        
        data = response.json()
        
        for item in data.get("items", []):
            if count >= max_results:
                break
            repo_name = item.get("full_name", "")
            if repo_name in known_repos:
                continue  # Already searched
            for model in self._find_vrm_in_repo_recursive(repo_name, max_results - count):
                yield model
                count += 1
                if count >= max_results:
                    break
    
    def _find_vrm_in_repo(self, repo_name: str, max_count: int) -> Iterator[ModelInfo]:
        """Find VRM files in a repository (top-level only)."""
        yield from self._find_vrm_in_repo_recursive(repo_name, max_count, max_depth=1)
    
    def _find_vrm_in_repo_recursive(
        self, 
        repo_name: str, 
        max_count: int, 
        path: str = "",
        max_depth: int = 3,
        current_depth: int = 0
    ) -> Iterator[ModelInfo]:
        """Find VRM files in a repository recursively."""
        if current_depth >= max_depth:
            return
        
        url = f"{self.API_BASE}/repos/{repo_name}/contents"
        if path:
            url = f"{url}/{path}"
        
        response = self.client.get(url, headers=self.headers)
        
        if response.status_code != 200:
            return
        
        contents = response.json()
        if not isinstance(contents, list):
            return
        
        count = 0
        dirs_to_search = []
        
        for item in contents:
            if count >= max_count:
                break
            
            name = item.get("name", "")
            item_type = item.get("type", "")
            
            if item_type == "file" and name.lower().endswith(".vrm"):
                download_url = item.get("download_url", "")
                html_url = item.get("html_url", "")
                item_path = item.get("path", name)
                
                yield ModelInfo(
                    source_model_id=f"{repo_name}/{item_path}",
                    name=name,
                    artist=repo_name.split("/")[0] if "/" in repo_name else "",
                    source_url=html_url,
                    is_downloadable=bool(download_url),
                    license=self._get_repo_license(repo_name),
                    license_url=f"https://github.com/{repo_name}/blob/main/LICENSE",
                    download_url=download_url,
                )
                count += 1
            elif item_type == "dir" and current_depth < max_depth - 1:
                # Queue directories for recursive search
                dir_path = item.get("path", name)
                # Skip common non-model directories
                if name.lower() not in ["node_modules", ".git", "dist", "build", "__pycache__"]:
                    dirs_to_search.append(dir_path)
        
        # Search subdirectories
        for dir_path in dirs_to_search:
            if count >= max_count:
                break
            for model in self._find_vrm_in_repo_recursive(
                repo_name, 
                max_count - count, 
                dir_path,
                max_depth,
                current_depth + 1
            ):
                yield model
                count += 1
                if count >= max_count:
                    break
    
    def _parse_code_result(self, item: dict) -> Optional[ModelInfo]:
        """Parse code search result into ModelInfo."""
        name = item.get("name", "")
        if not name.lower().endswith(".vrm"):
            return None
        
        repo = item.get("repository", {})
        repo_name = repo.get("full_name", "")
        path = item.get("path", "")
        
        # Construct download URL
        download_url = f"https://raw.githubusercontent.com/{repo_name}/main/{path}"
        html_url = item.get("html_url", "")
        
        return ModelInfo(
            source_model_id=f"{repo_name}/{path}",
            name=name,
            artist=repo.get("owner", {}).get("login", ""),
            source_url=html_url,
            is_downloadable=True,
            license=self._get_repo_license(repo_name),
            license_url=f"https://github.com/{repo_name}/blob/main/LICENSE",
            download_url=download_url,
        )
    
    def _get_repo_license(self, repo_name: str) -> str:
        """Get license info for a repository."""
        url = f"{self.API_BASE}/repos/{repo_name}/license"
        response = self.client.get(url, headers=self.headers)
        
        if response.status_code == 200:
            data = response.json()
            license_info = data.get("license", {})
            return license_info.get("name", "Unknown")
        
        return "Unknown"
    
    def _parse_next_link(self, link_header: str) -> Optional[str]:
        """Parse GitHub Link header for next page URL."""
        if not link_header:
            return None
        
        for part in link_header.split(","):
            if 'rel="next"' in part:
                match = re.search(r'<([^>]+)>', part)
                if match:
                    return match.group(1)
        
        return None
    
    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        """Download a VRM file from GitHub."""
        if not model.download_url:
            raise ValueError(f"No download URL for model {model.source_model_id}")
        
        # Convert GitHub URL to raw URL if needed
        url = model.download_url
        if "github.com" in url and "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com")
            url = url.replace("/blob/", "/")
        
        # Generate safe filename
        safe_name = model.source_model_id.replace("/", "_").replace("\\", "_")
        if not safe_name.lower().endswith(".vrm"):
            safe_name += ".vrm"
        
        output_path = output_dir / f"github_{safe_name}"
        return self.client.download_file(url, output_path, headers=self.headers)
