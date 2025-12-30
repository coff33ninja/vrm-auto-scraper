"""DeviantArt source implementation with OAuth 2.0 support."""
import json
import logging
import secrets
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode, urlparse, parse_qs

from .base import BaseSource, ModelInfo, RateLimitedClient

logger = logging.getLogger(__name__)


class DeviantArtOAuthHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""
    
    def do_GET(self) -> None:
        """Handle OAuth callback GET request."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        self.server.auth_code = params.get("code", [None])[0]
        self.server.auth_state = params.get("state", [None])[0]
        self.server.auth_error = params.get("error", [None])[0]
        
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        if self.server.auth_code:
            html = "<html><body><h1>DeviantArt Authorization successful!</h1><p>You can close this window.</p></body></html>"
        else:
            html = f"<html><body><h1>Authorization failed</h1><p>Error: {self.server.auth_error}</p></body></html>"
        
        self.wfile.write(html.encode())
    
    def log_message(self, format: str, *args) -> None:
        """Suppress HTTP server logs."""
        pass


class DeviantArtOAuth:
    """OAuth 2.0 handler for DeviantArt API."""
    
    AUTH_URL = "https://www.deviantart.com/oauth2/authorize"
    TOKEN_URL = "https://www.deviantart.com/oauth2/token"
    API_BASE = "https://www.deviantart.com/api/v1/oauth2"
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "http://localhost:8911/callback",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.client = RateLimitedClient(rate_limit_delay=0.5)
    
    def get_authorization_url(self) -> tuple[str, str]:
        """Generate authorization URL."""
        state = secrets.token_urlsafe(32)
        
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "browse",
            "state": state,
        }
        
        auth_url = f"{self.AUTH_URL}?{urlencode(params)}"
        return auth_url, state
    
    def authorize_interactive(self, port: int = 8911) -> dict:
        """Run interactive OAuth flow with local callback server."""
        auth_url, state = self.get_authorization_url()
        
        server = HTTPServer(("localhost", port), DeviantArtOAuthHandler)
        server.auth_code = None
        server.auth_state = None
        server.auth_error = None
        
        logger.info("Opening browser for DeviantArt authorization...")
        logger.info("If browser doesn't open, visit: %s", auth_url)
        webbrowser.open(auth_url)
        
        logger.info("Waiting for authorization callback...")
        server.handle_request()
        
        if server.auth_error:
            raise ValueError(f"Authorization failed: {server.auth_error}")
        
        if not server.auth_code:
            raise ValueError("No authorization code received")
        
        if server.auth_state != state:
            raise ValueError("State mismatch - possible CSRF attack")
        
        return self.exchange_code(server.auth_code)
    
    def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for access token."""
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }
        
        response = self.client.post(self.TOKEN_URL, data=data)
        response.raise_for_status()
        return response.json()
    
    def refresh_token(self, refresh_token: str) -> dict:
        """Refresh an expired access token."""
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }
        
        response = self.client.post(self.TOKEN_URL, data=data)
        response.raise_for_status()
        return response.json()



class DeviantArtSource(BaseSource):
    """
    DeviantArt API source for downloading 3D models.
    
    Requires OAuth 2.0 access token.
    Register your app at https://www.deviantart.com/developers/
    """
    
    API_BASE = "https://www.deviantart.com/api/v1/oauth2"
    
    def __init__(
        self,
        access_token: str,
        rate_limit_delay: float = 1.0,
    ):
        if not access_token:
            raise ValueError("DeviantArt access token is required")
        
        self.access_token = access_token
        self.client = RateLimitedClient(rate_limit_delay=rate_limit_delay)
    
    def get_source_name(self) -> str:
        return "deviantart"
    
    def _get_headers(self) -> dict:
        """Get headers with auth token."""
        return {"Authorization": f"Bearer {self.access_token}"}
    
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        """Search for downloadable 3D models on DeviantArt."""
        count = 0
        seen_ids: set[str] = set()
        
        # Search terms for 3D models (DeviantArt uses tags without spaces)
        search_terms = [
            "3Dmodel",
            "VRMmodel",
            "VRChat",
            "MMDmodel",
            "3Dcharacter",
            "3Davatar",
            "anime3D",
            "VRoid",
            "3Danime",
            "charactermodel",
            "freemodel",
            "downloadable3D",
        ]
        
        # Add user keywords
        if keywords:
            search_terms = keywords + search_terms
        
        for term in search_terms:
            if count >= max_results:
                break
            
            for model in self._browse_tag(term, max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids and model.is_downloadable:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1
    
    def _browse_tag(self, tag: str, max_count: int) -> Iterator[ModelInfo]:
        """Browse deviations by tag."""
        url = f"{self.API_BASE}/browse/tags"
        offset = 0
        limit = 24
        
        yielded = 0
        while yielded < max_count:
            params = {
                "tag": tag,
                "offset": offset,
                "limit": min(limit, max_count - yielded),
                "mature_content": "true",
            }
            
            response = self.client.get(url, headers=self._get_headers(), params=params)
            
            if response.status_code != 200:
                logger.warning(f"DeviantArt browse failed: {response.status_code}")
                break
            
            data = response.json()
            results = data.get("results", [])
            
            if not results:
                break
            
            for deviation in results:
                if yielded >= max_count:
                    break
                
                model = self._parse_deviation(deviation)
                if model:
                    yield model
                    yielded += 1
            
            if not data.get("has_more", False):
                break
            
            offset = data.get("next_offset", offset + limit)
    
    def _parse_deviation(self, deviation: dict) -> ModelInfo | None:
        """Parse deviation into ModelInfo."""
        deviation_id = deviation.get("deviationid", "")
        
        # Check if downloadable
        is_downloadable = deviation.get("is_downloadable", False)
        
        # Get content info for file type detection
        content = deviation.get("content", {})
        file_size = content.get("filesize", 0)
        
        # Get thumbnail - prefer content src, fallback to thumbs
        thumbnail_url = content.get("src")
        if not thumbnail_url:
            thumbs = deviation.get("thumbs", [])
            if thumbs:
                # Get largest thumbnail
                thumbnail_url = thumbs[-1].get("src")
        
        # Get author info
        author = deviation.get("author", {})
        
        # Log file size if available for debugging
        if file_size > 0:
            logger.debug(f"Deviation {deviation_id} has file size: {file_size}")
        
        return ModelInfo(
            source_model_id=str(deviation_id),
            name=deviation.get("title", f"Deviation {deviation_id}"),
            artist=author.get("username", "Unknown"),
            source_url=deviation.get("url", f"https://www.deviantart.com/deviation/{deviation_id}"),
            is_downloadable=is_downloadable,
            license="DeviantArt Terms",
            license_url="https://www.deviantart.com/about/policy/submission/",
            thumbnail_url=thumbnail_url,
        )
    
    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        """Download a deviation file from DeviantArt."""
        # Get download URL
        url = f"{self.API_BASE}/deviation/download/{model.source_model_id}"
        
        response = self.client.get(url, headers=self._get_headers())
        response.raise_for_status()
        
        data = response.json()
        download_url = data.get("src")
        filename = data.get("filename", f"deviantart_{model.source_model_id}")
        
        if not download_url:
            raise ValueError(f"No download URL for deviation {model.source_model_id}")
        
        # Determine file extension
        ext = Path(filename).suffix or ".zip"
        output_path = output_dir / f"deviantart_{model.source_model_id}{ext}"
        
        logger.info(f"Downloading from DeviantArt: {filename}")
        return self.client.download_file(download_url, output_path)


def save_tokens(tokens: dict, path: Path) -> None:
    """Save OAuth tokens to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(tokens, f, indent=2)
    logger.info(f"DeviantArt tokens saved to {path}")


def load_tokens(path: Path) -> dict | None:
    """Load OAuth tokens from a JSON file."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)
