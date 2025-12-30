"""VRM Pipeline - orchestrates extraction, conversion, and storage."""
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from archive import ArchiveHandler, is_skippable
from converter import convert_to_vrm, vrm_exists_for
from storage import DownloadRecord, DownloadsTracker, MetadataStore, ModelRecord

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result of scanning an extracted directory."""
    extract_dir: Path
    vrm_files: list[Path]
    convertible_files: list[Path]
    skipped_files: list[tuple[Path, str]]  # (path, reason)


@dataclass
class ConversionResult:
    """Result of converting a single file."""
    input_path: Path
    output_path: Path | None
    success: bool
    error: str | None = None
    original_format: str | None = None


class VRMPipeline:
    """Orchestrates the VRM conversion pipeline."""
    
    # Supported formats for conversion
    CONVERTIBLE_EXTENSIONS = {".fbx", ".obj", ".blend", ".glb"}
    
    def __init__(
        self,
        store: MetadataStore,
        downloads: DownloadsTracker,
        extract_dir: Path,
    ):
        self.store = store
        self.downloads = downloads
        self.archive_handler = ArchiveHandler(extract_dir)
        self.extract_dir = extract_dir
    
    def should_download(self, source: str, model_id: str) -> bool:
        """Check if a model should be downloaded (not already in downloads table)."""
        return not self.downloads.exists(source, model_id)
    
    def scan_directory(
        self,
        directory: Path,
        thumbnail_path: Path | None = None,
    ) -> ScanResult:
        """
        Scan a directory for 3D model files.
        
        Args:
            directory: Directory to scan
            thumbnail_path: Optional thumbnail for AI classification
        
        Returns categorized files: VRM, convertible, and skipped.
        """
        vrm_files: list[Path] = []
        convertible_files: list[Path] = []
        skipped_files: list[tuple[Path, str]] = []
        
        # Convert thumbnail_path to Path if string
        thumb_path = Path(thumbnail_path) if thumbnail_path else None
        
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            
            ext = file_path.suffix.lower()
            
            # Check if should be skipped (with AI classification)
            should_skip, reason = is_skippable(file_path, thumb_path)
            if should_skip:
                skipped_files.append((file_path, reason))
                continue
            
            # Categorize by type
            if ext == ".vrm":
                vrm_files.append(file_path)
            elif ext in self.CONVERTIBLE_EXTENSIONS:
                # Skip if VRM already exists for this file
                if vrm_exists_for(file_path):
                    logger.debug(f"Skipping {file_path.name} - VRM already exists")
                    continue
                convertible_files.append(file_path)
        
        return ScanResult(
            extract_dir=directory,
            vrm_files=vrm_files,
            convertible_files=convertible_files,
            skipped_files=skipped_files,
        )
    
    def convert_file(self, file_path: Path) -> ConversionResult:
        """
        Convert a single file to VRM.
        
        Returns ConversionResult with success/failure info.
        """
        original_format = file_path.suffix.lower().lstrip(".")
        
        try:
            output_path = convert_to_vrm(file_path)
            if output_path:
                return ConversionResult(
                    input_path=file_path,
                    output_path=output_path,
                    success=True,
                    original_format=original_format,
                )
            else:
                return ConversionResult(
                    input_path=file_path,
                    output_path=None,
                    success=False,
                    error="Conversion returned None",
                    original_format=original_format,
                )
        except Exception as e:
            logger.error(f"Conversion failed for {file_path}: {e}")
            return ConversionResult(
                input_path=file_path,
                output_path=None,
                success=False,
                error=str(e),
                original_format=original_format,
            )
    
    def process_download(
        self,
        source: str,
        model_id: str,
        file_path: Path,
        name: str,
        artist: str = "",
        source_url: str = "",
        license_info: str | None = None,
        thumbnail_path: str | None = None,
    ) -> list[ModelRecord]:
        """
        Process a downloaded file through the full pipeline.
        
        Steps:
        1. Extract if archive
        2. Scan for 3D files
        3. Convert to VRM
        4. Store in database
        
        Returns list of created ModelRecord entries.
        """
        created_records: list[ModelRecord] = []
        timestamp = datetime.now().isoformat()
        
        # Track the download
        download_record = DownloadRecord(
            source=source,
            source_model_id=model_id,
            source_url=source_url,
            downloaded_at=timestamp,
            raw_path=str(file_path),
            status="downloaded",
        )
        
        try:
            self.downloads.add(download_record)
        except Exception:
            # Already exists, update status
            self.downloads.update_status(source, model_id, "downloaded")
        
        # Process the file
        ext = file_path.suffix.lower()
        
        # If it's an archive, extract it
        if ext in {".zip", ".rar", ".7z"}:
            self.archive_handler.process(file_path, source, model_id)
            scan_dir = self.extract_dir / source / model_id
            self.downloads.update_status(source, model_id, "extracted")
        else:
            # Single file - scan its directory
            scan_dir = file_path.parent
        
        # Scan for 3D files (pass thumbnail for AI classification)
        thumb_path = Path(thumbnail_path) if thumbnail_path else None
        scan_result = self.scan_directory(scan_dir, thumb_path)
        
        # Log skipped files
        for skipped_path, reason in scan_result.skipped_files:
            logger.info(f"Skipped: {skipped_path.name} ({reason})")
        
        # Add existing VRM files directly
        for vrm_path in scan_result.vrm_files:
            record = self._create_model_record(
                vrm_path=vrm_path,
                source=source,
                model_id=model_id,
                name=name,
                artist=artist,
                source_url=source_url,
                license_info=license_info,
                thumbnail_path=thumbnail_path,
                original_format="vrm",
                timestamp=timestamp,
            )
            if record:
                created_records.append(record)
        
        # Convert and add convertible files
        for conv_path in scan_result.convertible_files:
            result = self.convert_file(conv_path)
            
            if result.success and result.output_path:
                record = self._create_model_record(
                    vrm_path=result.output_path,
                    source=source,
                    model_id=model_id,
                    name=name,
                    artist=artist,
                    source_url=source_url,
                    license_info=license_info,
                    thumbnail_path=thumbnail_path,
                    original_format=result.original_format,
                    timestamp=timestamp,
                )
                if record:
                    created_records.append(record)
            else:
                logger.warning(f"Failed to convert {conv_path.name}: {result.error}")
        
        # Update download status
        if created_records:
            self.downloads.update_status(source, model_id, "converted")
        elif scan_result.convertible_files:
            self.downloads.update_status(source, model_id, "failed", "All conversions failed")
        
        return created_records
    
    def _create_model_record(
        self,
        vrm_path: Path,
        source: str,
        model_id: str,
        name: str,
        artist: str,
        source_url: str,
        license_info: str | None,
        thumbnail_path: str | None,
        original_format: str | None,
        timestamp: str,
    ) -> ModelRecord | None:
        """Create and store a ModelRecord for a VRM file."""
        # Generate unique model ID for this specific file
        file_model_id = f"{model_id}_{vrm_path.stem}"
        
        # Check if already exists
        if self.store.exists(source, file_model_id):
            logger.debug(f"Model already in database: {file_model_id}")
            return None
        
        record = ModelRecord(
            source=source,
            source_model_id=file_model_id,
            name=f"{name} - {vrm_path.stem}" if vrm_path.stem != name else name,
            artist=artist,
            source_url=source_url,
            license=license_info,
            acquired_at=timestamp,
            file_path=str(vrm_path),
            file_type="vrm",
            size_bytes=vrm_path.stat().st_size,
            thumbnail_path=thumbnail_path,
            original_format=original_format,
        )
        
        try:
            self.store.add(record)
            logger.info(f"Added to database: {record.name} (from {original_format})")
            return record
        except Exception as e:
            logger.error(f"Failed to add record: {e}")
            return None
