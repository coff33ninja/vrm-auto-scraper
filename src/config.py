"""Configuration management for VRM Auto-Scraper."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""
    
    def __init__(self):
        # VRoid Hub OAuth 2.0 credentials
        self.vroid_client_id: str = os.getenv("VROID_CLIENT_ID", "")
        self.vroid_client_secret: str = os.getenv("VROID_CLIENT_SECRET", "")
        self.vroid_access_token: str = os.getenv("VROID_ACCESS_TOKEN", "")
        self.vroid_refresh_token: str = os.getenv("VROID_REFRESH_TOKEN", "")
        
        # Other API tokens
        self.sketchfab_api_token: str = os.getenv("SKETCHFAB_API_TOKEN", "")
        self.github_token: str = os.getenv("GITHUB_TOKEN", "")
        
        # Settings
        self.rate_limit_delay: float = float(os.getenv("RATE_LIMIT_DELAY", "1.0"))
        self.data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
        
        # Ensure data directories exist
        self.raw_dir = self.data_dir / "raw"
        self.extracted_dir = self.data_dir / "extracted"
        self.db_path = self.data_dir / "models.db"
    
    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
    
    def has_vroid_credentials(self) -> bool:
        """Check if VRoid Hub OAuth credentials are configured."""
        return bool(self.vroid_client_id and self.vroid_client_secret)
    
    def has_vroid_token(self) -> bool:
        """Check if VRoid Hub access token is available."""
        return bool(self.vroid_access_token)
    
    def has_sketchfab_token(self) -> bool:
        """Check if Sketchfab token is configured."""
        return bool(self.sketchfab_api_token)
    
    def has_github_token(self) -> bool:
        """Check if GitHub token is configured."""
        return bool(self.github_token)


# Global config instance
config = Config()
