"""VRoid Hub source implementation with OAuth 2.0 PKCE support."""
import base64
import hashlib
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


class VRoidOAuthHandler(BaseHTTPRequestHandler):
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
            html = "<html><body><h1>Authorization successful!</h1><p>You can close this window.</p></body></html>"
        else:
            html = f"<html><body><h1>Authorization failed</h1><p>Error: {self.server.auth_error}</p></body></html>"
        
        self.wfile.write(html.encode())
    
    def log_message(self, format: str, *args) -> None:
        """Suppress HTTP server logs."""
        pass


class VRoidHubOAuth:
    """
    OAuth 2.0 with PKCE handler for VRoid Hub.
    
    Implements the authorization code flow with PKCE as required by VRoid Hub API.
    """
    
    AUTH_URL = "https://hub.vroid.com/oauth/authorize"
    TOKEN_URL = "https://hub.vroid.com/oauth/token"
    REVOKE_URL = "https://hub.vroid.com/oauth/revoke"
    API_VERSION = "11"
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "http://localhost:8910/callback",
        scope: str = "default",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.client = RateLimitedClient(rate_limit_delay=0.5)
    
    def _generate_pkce(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge."""
        # Generate code_verifier (43-128 chars, [A-Za-z0-9-._~])
        code_verifier = secrets.token_urlsafe(64)[:128]
        
        # Generate code_challenge = base64url(sha256(code_verifier))
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        
        return code_verifier, code_challenge
    
    def get_authorization_url(self) -> tuple[str, str, str]:
        """
        Generate authorization URL with PKCE.
        
        Returns:
            Tuple of (auth_url, state, code_verifier)
        """
        state = secrets.token_urlsafe(32)
        code_verifier, code_challenge = self._generate_pkce()
        
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        
        auth_url = f"{self.AUTH_URL}?{urlencode(params)}"
        return auth_url, state, code_verifier

    def authorize_interactive(self, port: int = 8910) -> dict:
        """
        Run interactive OAuth flow with local callback server.
        
        Opens browser for user authorization and captures the callback.
        
        Args:
            port: Local port for callback server
            
        Returns:
            Token response dict with access_token, refresh_token, etc.
        """
        auth_url, state, code_verifier = self.get_authorization_url()
        
        # Start local server for callback
        server = HTTPServer(("localhost", port), VRoidOAuthHandler)
        server.auth_code = None
        server.auth_state = None
        server.auth_error = None
        
        logger.info("Opening browser for VRoid Hub authorization...")
        logger.info("If browser doesn't open, visit: %s", auth_url)
        webbrowser.open(auth_url)
        
        # Wait for callback
        logger.info("Waiting for authorization callback...")
        server.handle_request()
        
        if server.auth_error:
            raise ValueError(f"Authorization failed: {server.auth_error}")
        
        if not server.auth_code:
            raise ValueError("No authorization code received")
        
        if server.auth_state != state:
            raise ValueError("State mismatch - possible CSRF attack")
        
        # Exchange code for tokens
        return self.exchange_code(server.auth_code, code_verifier)
    
    def exchange_code(self, code: str, code_verifier: str) -> dict:
        """
        Exchange authorization code for access token.
        
        Args:
            code: Authorization code from callback
            code_verifier: PKCE code verifier
            
        Returns:
            Token response dict
        """
        headers = {"X-Api-Version": self.API_VERSION}
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
        }
        
        response = self.client.post(self.TOKEN_URL, headers=headers, data=data)
        response.raise_for_status()
        return response.json()
    
    def refresh_token(self, refresh_token: str) -> dict:
        """
        Refresh an expired access token.
        
        Args:
            refresh_token: The refresh token
            
        Returns:
            New token response dict
        """
        headers = {"X-Api-Version": self.API_VERSION}
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }
        
        response = self.client.post(self.TOKEN_URL, headers=headers, data=data)
        response.raise_for_status()
        return response.json()
    
    def revoke_token(self, access_token: str) -> None:
        """Revoke an access token."""
        headers = {
            "X-Api-Version": self.API_VERSION,
            "Authorization": f"Bearer {access_token}",
        }
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "token": access_token,
        }
        
        response = self.client.post(self.REVOKE_URL, headers=headers, data=data)
        response.raise_for_status()


class VRoidHubSource(BaseSource):
    """
    VRoid Hub API source for downloading VRM models.
    
    Requires OAuth 2.0 access token obtained via VRoidHubOAuth.
    Register your app at https://hub.vroid.com/oauth/applications
    
    API Documentation: https://developer.vroid.com/en/api/
    """
    
    API_BASE = "https://hub.vroid.com/api"
    API_VERSION = "11"
    
    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        rate_limit_delay: float = 1.0,
    ):
        """
        Initialize VRoid Hub source.
        
        Args:
            access_token: OAuth 2.0 Bearer token
            client_id: Application ID (needed for hearts endpoint)
            rate_limit_delay: Delay between API requests
        """
        if not access_token:
            raise ValueError("VRoid Hub access token is required")
        
        self.access_token = access_token
        self.client_id = client_id
        self.client = RateLimitedClient(rate_limit_delay=rate_limit_delay)
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Api-Version": self.API_VERSION,
        }
    
    def get_source_name(self) -> str:
        return "vroid_hub"
    
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        """
        Search for downloadable VRM models on VRoid Hub.
        
        Uses multiple endpoints and search strategies:
        1. Keyword search with provided keywords
        2. Keyword search with common VRM-related terms
        3. Staff picks for curated models
        4. User's hearted models
        5. User's own uploaded models
        """
        count = 0
        seen_ids: set[str] = set()
        
        # Search by provided keywords
        if keywords:
            for model in self._search_models(keywords, max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids and model.is_downloadable:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1
        
        # Try additional search terms to find more models
        additional_terms = [
            ["free", "download"],
            ["character"],
            ["anime"],
            ["girl"],
            ["boy"],
            ["cute"],
            ["original"],
            # HoYoverse games
            ["genshin"],
            ["genshin impact"],
            ["honkai"],
            ["honkai star rail"],
            ["honkai impact"],
            ["zenless zone zero"],
            ["zzz"],
            # Popular anime/games
            ["vtuber"],
            ["hololive"],
            ["nijisanji"],
            ["miku"],
            ["hatsune"],
            ["touhou"],
            ["fate"],
            ["blue archive"],
            ["arknights"],
            ["azur lane"],
            ["uma musume"],
            ["project sekai"],
            ["nier"],
            ["final fantasy"],
            ["persona"],
        ]
        
        for terms in additional_terms:
            if count >= max_results:
                break
            for model in self._search_models(terms, max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids and model.is_downloadable:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1
        
        # Get staff picks
        if count < max_results:
            for model in self._get_staff_picks(max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids and model.is_downloadable:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1
        
        # Get user's hearted models
        if count < max_results and self.client_id:
            for model in self._get_hearted_models(max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids and model.is_downloadable:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1
        
        # Get user's own models
        if count < max_results:
            for model in self._get_account_models(max_results - count):
                if count >= max_results:
                    break
                if model.source_model_id not in seen_ids and model.is_downloadable:
                    seen_ids.add(model.source_model_id)
                    yield model
                    count += 1

    def _search_models(self, keywords: list[str], max_count: int) -> Iterator[ModelInfo]:
        """Search character models by keyword."""
        keyword = " ".join(keywords)
        url = f"{self.API_BASE}/search/character_models"
        params = {
            "keyword": keyword,
            "count": min(max_count, 100),
            "is_downloadable": "true",
        }
        
        yielded = 0
        while yielded < max_count:
            response = self.client.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                logger.warning(f"Search failed: {response.status_code}")
                break
            
            data = response.json()
            models = data.get("data", [])
            
            if not models:
                break
            
            for model in models:
                if yielded >= max_count:
                    break
                yield self._parse_model(model)
                yielded += 1
            
            # Check for next page - handle relative URLs
            next_href = data.get("_links", {}).get("next", {}).get("href")
            if not next_href:
                break
            
            # Convert relative URL to absolute
            if next_href.startswith("/"):
                url = f"https://hub.vroid.com{next_href}"
            else:
                url = next_href
            params = {}
    
    def _get_staff_picks(self, max_count: int) -> Iterator[ModelInfo]:
        """Get staff-picked character models."""
        url = f"{self.API_BASE}/staff_picks"
        params = {"count": min(max_count, 100)}
        
        yielded = 0
        while yielded < max_count:
            response = self.client.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                logger.warning(f"Staff picks failed: {response.status_code}")
                break
            
            data = response.json()
            models = data.get("data", [])
            
            if not models:
                break
            
            for model in models:
                if yielded >= max_count:
                    break
                yield self._parse_model(model)
                yielded += 1
            
            # Check for next page
            next_href = data.get("_links", {}).get("next", {}).get("href")
            if not next_href:
                break
            
            if next_href.startswith("/"):
                url = f"https://hub.vroid.com{next_href}"
            else:
                url = next_href
            params = {}
    
    def _get_hearted_models(self, max_count: int) -> Iterator[ModelInfo]:
        """Get user's hearted (liked) character models."""
        if not self.client_id:
            return
        
        url = f"{self.API_BASE}/hearts"
        params = {
            "application_id": self.client_id,
            "count": min(max_count, 100),
            "is_downloadable": "true",
        }
        
        yielded = 0
        while yielded < max_count:
            response = self.client.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                logger.warning(f"Hearts failed: {response.status_code}")
                break
            
            data = response.json()
            models = data.get("data", [])
            
            if not models:
                break
            
            for model in models:
                if yielded >= max_count:
                    break
                yield self._parse_model(model)
                yielded += 1
            
            # Check for next page - handle relative URLs
            next_href = data.get("_links", {}).get("next", {}).get("href")
            if not next_href:
                break
            
            # Convert relative URL to absolute
            if next_href.startswith("/"):
                url = f"https://hub.vroid.com{next_href}"
            else:
                url = next_href
            params = {}

    def _get_account_models(self, max_count: int) -> Iterator[ModelInfo]:
        """Get user's own uploaded character models."""
        url = f"{self.API_BASE}/account/character_models"
        params = {"count": min(max_count, 100)}
        
        yielded = 0
        while yielded < max_count:
            response = self.client.get(url, headers=self.headers, params=params)
            
            if response.status_code != 200:
                logger.warning(f"Account models failed: {response.status_code}")
                break
            
            data = response.json()
            models = data.get("data", [])
            
            if not models:
                break
            
            for model in models:
                if yielded >= max_count:
                    break
                yield self._parse_model(model)
                yielded += 1
            
            # Check for next page - handle relative URLs
            next_href = data.get("_links", {}).get("next", {}).get("href")
            if not next_href:
                break
            
            # Convert relative URL to absolute
            if next_href.startswith("/"):
                url = f"https://hub.vroid.com{next_href}"
            else:
                url = next_href
            params = {}
    
    def _parse_model(self, model: dict) -> ModelInfo:
        """Parse API response into ModelInfo."""
        model_id = model.get("id", "")
        character = model.get("character", {})
        user = character.get("user", {}) or model.get("user", {})
        
        # Extract license info
        license_info = model.get("license", {})
        license_parts = []
        if license_info.get("modification") == "allow":
            license_parts.append("modification allowed")
        if license_info.get("redistribution") == "allow":
            license_parts.append("redistribution allowed")
        if license_info.get("personal_commercial_use") in ("allow", "profit", "nonprofit"):
            license_parts.append("personal commercial use")
        
        license_str = "VRoid Hub License"
        if license_parts:
            license_str += f" ({', '.join(license_parts)})"
        
        # Build source URL
        char_id = character.get("id", "")
        if char_id:
            source_url = f"https://hub.vroid.com/characters/{char_id}/models/{model_id}"
        else:
            source_url = f"https://hub.vroid.com/characters/{model_id}"
        
        # Extract thumbnail URL (portrait image)
        thumbnail_url = None
        portrait = model.get("portrait_image", {})
        if portrait:
            # Prefer w300 size, fallback to original
            w300 = portrait.get("w300", {})
            if w300:
                thumbnail_url = w300.get("url")
            if not thumbnail_url:
                original = portrait.get("original", {})
                thumbnail_url = original.get("url")
        
        return ModelInfo(
            source_model_id=str(model_id),
            name=character.get("name", "") or model.get("name", f"Model {model_id}"),
            artist=user.get("name", "Unknown"),
            source_url=source_url,
            is_downloadable=model.get("is_downloadable", False),
            license=license_str,
            license_url="https://hub.vroid.com/license",
            thumbnail_url=thumbnail_url,
        )
    
    def get_model_details(self, model_id: str) -> dict:
        """Get detailed information about a specific model."""
        url = f"{self.API_BASE}/character_models/{model_id}"
        response = self.client.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json().get("data", {})

    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        """
        Download a VRM model from VRoid Hub.
        
        Flow:
        1. POST /api/download_licenses with character_model_id
        2. GET /api/download_licenses/{id}/download (redirects to S3 presigned URL)
        
        Args:
            model: ModelInfo with source_model_id
            output_dir: Directory to save the downloaded file
            
        Returns:
            Path to the downloaded VRM file
        """
        # Step 1: Issue download license
        license_url = f"{self.API_BASE}/download_licenses"
        response = self.client.post(
            license_url,
            headers=self.headers,
            json={"character_model_id": model.source_model_id}
        )
        response.raise_for_status()
        
        license_data = response.json()
        license_id = license_data.get("data", {}).get("id")
        
        if not license_id:
            raise ValueError(f"Failed to get download license for model {model.source_model_id}")
        
        logger.info(f"Obtained download license: {license_id}")
        
        # Step 2: Get download URL (follows redirect to S3)
        download_url = f"{self.API_BASE}/download_licenses/{license_id}/download"
        
        # Don't follow redirects automatically to get the S3 URL
        response = self.client.get(
            download_url,
            headers=self.headers,
            allow_redirects=False
        )
        
        output_path = output_dir / f"vroid_{model.source_model_id}.vrm"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if response.status_code == 302:
            # Get the S3 presigned URL from redirect
            s3_url = response.headers.get("Location")
            if s3_url:
                logger.info("Downloading from S3...")
                return self.client.download_file(s3_url, output_path)
            else:
                raise ValueError("No redirect URL in 302 response")
        elif response.status_code == 200:
            # Content returned directly
            output_path.write_bytes(response.content)
            return output_path
        else:
            response.raise_for_status()
            raise ValueError(f"Unexpected response: {response.status_code}")


def save_tokens(tokens: dict, path: Path) -> None:
    """Save OAuth tokens to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(tokens, f, indent=2)
    logger.info(f"Tokens saved to {path}")


def load_tokens(path: Path) -> dict | None:
    """Load OAuth tokens from a JSON file."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)
