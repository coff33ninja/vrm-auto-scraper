"""Sketchfab source implementation."""
from pathlib import Path
from typing import Iterator

from .base import BaseSource, ModelInfo, RateLimitedClient


class SketchfabSource(BaseSource):
    """
    Sketchfab Data API v3 source for downloading 3D models.
    
    Requires API token from sketchfab.com/settings/password.
    Downloads models in GLB format (VRM not directly available via API).
    """
    
    API_BASE = "https://api.sketchfab.com/v3"
    
    # Free Creative Commons licenses
    FREE_LICENSES = [
        "cc0",      # CC0 - Public Domain
        "cc-by",    # CC BY - Attribution
        "cc-by-sa", # CC BY-SA - Attribution ShareAlike
        "cc-by-nd", # CC BY-ND - Attribution NoDerivatives
        "cc-by-nc", # CC BY-NC - Attribution NonCommercial
        "cc-by-nc-sa",  # CC BY-NC-SA
        "cc-by-nc-nd",  # CC BY-NC-ND
    ]
    
    def __init__(self, api_token: str, rate_limit_delay: float = 1.0):
        if not api_token:
            raise ValueError("Sketchfab API token is required")
        
        self.api_token = api_token
        self.client = RateLimitedClient(rate_limit_delay=rate_limit_delay)
        self.headers = {"Authorization": f"Token {api_token}"}
    
    def get_source_name(self) -> str:
        return "sketchfab"
    
    # Extended search terms for finding more avatar models
    SEARCH_TERMS = [
        ["vrm", "avatar"],
        ["vroid", "avatar"],
        ["vrchat", "avatar"],
        ["anime", "character"],
        ["genshin", "impact"],
        ["honkai", "star rail"],
        ["zenless", "zone zero"],
        ["hololive", "vtuber"],
        ["nijisanji"],
        ["miku", "hatsune"],
        ["touhou"],
        ["fate", "grand order"],
        ["blue archive"],
        ["arknights"],
        ["azur lane"],
        ["nier", "automata"],
        ["final fantasy"],
        ["persona"],
        ["anime", "girl"],
        ["anime", "boy"],
    ]
    
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        """
        Search for downloadable models on Sketchfab.
        
        Filters by:
        - downloadable=true
        - Free licenses (CC or standard)
        - Multiple search terms for broader coverage
        """
        seen_ids: set[str] = set()
        count = 0
        
        # Start with provided keywords
        search_queries = [keywords] if keywords else []
        search_queries.extend(self.SEARCH_TERMS)
        
        for terms in search_queries:
            if count >= max_results:
                break
            
            query = " ".join(terms)
            url = f"{self.API_BASE}/search"
            params = {
                "type": "models",
                "q": query,
                "downloadable": "true",
                "count": min(max_results - count, 24),
            }
            
            # Get results for this query
            for model in self._search_query(url, params, max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1
    
    def _search_query(self, url: str, params: dict, max_count: int) -> Iterator[ModelInfo]:
        """Execute a single search query with pagination."""
        count = 0
        
        while count < max_count:
            response = self.client.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                break
            
            data = response.json()
            results = data.get("results", [])
            
            for item in results:
                if count >= max_count:
                    break
                
                model = self._parse_model(item)
                if model.is_downloadable:
                    yield model
                    count += 1
            
            # Check for next page
            next_url = data.get("next")
            if not next_url or len(results) == 0:
                break
            
            url = next_url
            params = {}  # Next URL includes all params
    
    def _parse_model(self, item: dict) -> ModelInfo:
        """Parse API response into ModelInfo."""
        model_id = item.get("uid", "")
        license_info = item.get("license", {}) or {}
        user = item.get("user", {}) or {}
        
        # Check if license is free (CC licenses or no license restriction)
        license_slug = license_info.get("slug", "")
        is_free_license = license_slug in self.FREE_LICENSES or not license_slug
        
        # Get thumbnail
        thumbnails = item.get("thumbnails", {}) or {}
        images = thumbnails.get("images", []) or []
        thumbnail_url = None
        for img in images:
            if img.get("width", 0) >= 200:
                thumbnail_url = img.get("url")
                break
        if not thumbnail_url and images:
            thumbnail_url = images[0].get("url")
        
        return ModelInfo(
            source_model_id=model_id,
            name=item.get("name", f"Model {model_id}"),
            artist=user.get("displayName", "") or user.get("username", ""),
            source_url=item.get("viewerUrl", f"https://sketchfab.com/3d-models/{model_id}"),
            is_downloadable=item.get("isDownloadable", False) and is_free_license,
            license=license_info.get("label", "Sketchfab Standard"),
            license_url=license_info.get("url", "https://sketchfab.com/licenses"),
            thumbnail_url=thumbnail_url,
        )
    
    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        """
        Download a model from Sketchfab.
        
        Flow:
        1. GET /models/{uid}/download to get download URLs
        2. Download the GLB/GLTF archive (without auth header for S3)
        """
        # Get download info
        download_url = f"{self.API_BASE}/models/{model.source_model_id}/download"
        response = self.client.get(download_url, headers=self.headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Prefer GLB format, fall back to GLTF
        glb_info = data.get("glb", {})
        gltf_info = data.get("gltf", {})
        
        if glb_info and glb_info.get("url"):
            file_url = glb_info["url"]
            ext = ".glb"
        elif gltf_info and gltf_info.get("url"):
            file_url = gltf_info["url"]
            ext = ".zip"  # GLTF comes as a ZIP with textures
        else:
            raise ValueError(f"No download URL found for model {model.source_model_id}")
        
        output_path = output_dir / f"sketchfab_{model.source_model_id}{ext}"
        # Don't pass auth headers to S3 presigned URL
        return self.client.download_file(file_url, output_path)
