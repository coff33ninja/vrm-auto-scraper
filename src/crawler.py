"""Crawler engine for orchestrating model downloads from multiple sources."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from archive import ArchiveHandler
from sources.base import BaseSource, ModelInfo
from storage import MetadataStore, ModelRecord, DownloadsTracker

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    """Result summary of a crawl operation."""
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class CrawlerEngine:
    """
    Orchestrates crawling and downloading models from multiple sources.
    
    Handles:
    - Iterating through sources
    - Checking for duplicates
    - Downloading and processing files
    - Storing metadata
    - Error handling and continuation
    """
    
    def __init__(
        self,
        sources: list[BaseSource],
        store: MetadataStore,
        archive_handler: ArchiveHandler,
        raw_dir: Path,
        thumbnails_dir: Optional[Path] = None,
        downloads_tracker: Optional[DownloadsTracker] = None,
        force_download: bool = False,
    ):
        self.sources = sources
        self.store = store
        self.archive_handler = archive_handler
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir = thumbnails_dir or (raw_dir.parent / "thumbnails")
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_tracker = downloads_tracker
        self.force_download = force_download
    
    def crawl(
        self,
        keywords: Optional[list[str]] = None,
        max_per_source: int = 100,
        skip_existing: bool = True,
    ) -> CrawlResult:
        """
        Crawl all sources, download models, and store metadata.
        
        Args:
            keywords: Search keywords to filter models
            max_per_source: Maximum models to download per source
            skip_existing: Skip models already in database
            
        Returns:
            CrawlResult with counts of downloaded/skipped/failed
        """
        result = CrawlResult()
        keywords = keywords or []
        
        for source in self.sources:
            source_name = source.get_source_name()
            logger.info(f"Crawling source: {source_name}")
            
            try:
                source_result = self._crawl_source(
                    source=source,
                    keywords=keywords,
                    max_results=max_per_source,
                    skip_existing=skip_existing,
                )
                
                result.downloaded += source_result.downloaded
                result.skipped += source_result.skipped
                result.failed += source_result.failed
                result.errors.extend(source_result.errors)
                
            except Exception as e:
                error_msg = f"Error crawling {source_name}: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
        
        return result
    
    def _crawl_source(
        self,
        source: BaseSource,
        keywords: list[str],
        max_results: int,
        skip_existing: bool,
    ) -> CrawlResult:
        """Crawl a single source."""
        result = CrawlResult()
        source_name = source.get_source_name()
        
        for model in source.search(keywords, max_results):
            try:
                # Check downloads tracker first (if available and not forcing)
                if self.downloads_tracker and not self.force_download:
                    if self.downloads_tracker.exists(source_name, model.source_model_id):
                        logger.debug(f"Skipping already downloaded: {model.name}")
                        result.skipped += 1
                        continue
                
                # Check for duplicates in models table
                if skip_existing and self.store.exists(source_name, model.source_model_id):
                    logger.debug(f"Skipping existing model: {model.name}")
                    result.skipped += 1
                    continue
                
                # Download and process
                record = self._download_and_process(source, model)
                
                if record:
                    self.store.add(record)
                    logger.info(f"Downloaded: {model.name} from {source_name}")
                    result.downloaded += 1
                    
            except Exception as e:
                error_msg = f"Failed to download {model.name}: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                result.failed += 1
                # Continue with next model
                continue
        
        return result
    
    def _download_and_process(
        self,
        source: BaseSource,
        model: ModelInfo,
    ) -> Optional[ModelRecord]:
        """Download a model and create a metadata record."""
        source_name = source.get_source_name()
        
        # Create source-specific output directory
        output_dir = self.raw_dir / source_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Download the file
        downloaded_path = source.download(model, output_dir)
        
        # Process the downloaded file
        processed = self.archive_handler.process(
            downloaded_path,
            source_name,
            model.source_model_id,
        )
        
        # Validate file exists and get size
        if not processed.primary_path.exists():
            raise ValueError(f"Downloaded file not found: {processed.primary_path}")
        
        actual_size = processed.primary_path.stat().st_size
        if actual_size != processed.size_bytes:
            # Update size if it changed (e.g., after extraction)
            processed.size_bytes = actual_size
        
        # Create metadata record
        thumbnail_path = self._download_thumbnail(model, source_name)
        
        record = ModelRecord(
            source=source_name,
            source_model_id=model.source_model_id,
            name=model.name,
            artist=model.artist,
            source_url=model.source_url,
            license=model.license,
            license_url=model.license_url,
            thumbnail_path=thumbnail_path,
            acquired_at=datetime.now(timezone.utc).isoformat(),
            file_path=str(processed.primary_path),
            file_type=processed.file_type,
            size_bytes=processed.size_bytes,
            notes=processed.notes,
        )
        
        # Handle multiple VRMs from archives
        if processed.additional_vrms:
            # Store additional VRMs as separate records
            for i, vrm_path in enumerate(processed.additional_vrms):
                additional_record = ModelRecord(
                    source=source_name,
                    source_model_id=f"{model.source_model_id}_extra_{i+1}",
                    name=f"{model.name} ({vrm_path.name})",
                    artist=model.artist,
                    source_url=model.source_url,
                    license=model.license,
                    license_url=model.license_url,
                    thumbnail_path=thumbnail_path,
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                    file_path=str(vrm_path),
                    file_type="vrm",
                    size_bytes=vrm_path.stat().st_size,
                    notes={"from_archive": str(downloaded_path)},
                )
                self.store.add(additional_record)
        
        return record
    
    def _download_thumbnail(self, model: ModelInfo, source_name: str) -> Optional[str]:
        """Download model thumbnail if available."""
        if not model.thumbnail_url:
            return None
        
        try:
            # Determine file extension from URL
            ext = ".png"
            if ".jpg" in model.thumbnail_url or ".jpeg" in model.thumbnail_url:
                ext = ".jpg"
            elif ".webp" in model.thumbnail_url:
                ext = ".webp"
            
            thumb_dir = self.thumbnails_dir / source_name
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = thumb_dir / f"{model.source_model_id}{ext}"
            
            # Skip if already downloaded
            if thumb_path.exists():
                return str(thumb_path)
            
            response = requests.get(model.thumbnail_url, timeout=30)
            response.raise_for_status()
            
            thumb_path.write_bytes(response.content)
            logger.debug(f"Downloaded thumbnail for {model.name}")
            return str(thumb_path)
            
        except Exception as e:
            logger.warning(f"Failed to download thumbnail for {model.name}: {e}")
            return None
