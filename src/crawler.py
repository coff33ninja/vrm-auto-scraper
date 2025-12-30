"""Crawler engine for orchestrating model downloads from multiple sources."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from archive import ArchiveHandler, ProcessedFile
from sources.base import BaseSource, ModelInfo
from storage import MetadataStore, ModelRecord

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
    ):
        self.sources = sources
        self.store = store
        self.archive_handler = archive_handler
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
    
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
                # Check for duplicates
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
        record = ModelRecord(
            source=source_name,
            source_model_id=model.source_model_id,
            name=model.name,
            artist=model.artist,
            source_url=model.source_url,
            license=model.license,
            license_url=model.license_url,
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
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                    file_path=str(vrm_path),
                    file_type="vrm",
                    size_bytes=vrm_path.stat().st_size,
                    notes={"from_archive": str(downloaded_path)},
                )
                self.store.add(additional_record)
        
        return record
