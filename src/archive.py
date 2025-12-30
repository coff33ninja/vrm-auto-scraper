"""Archive handling for downloaded VRM models and related files."""
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Try to import rarfile for RAR support
try:
    import rarfile
    HAS_RAR_SUPPORT = True
except ImportError:
    HAS_RAR_SUPPORT = False


@dataclass
class ProcessedFile:
    """Result of processing a downloaded file."""
    primary_path: Path
    file_type: str  # "vrm", "glb", "zip", or other extension
    size_bytes: int
    notes: dict = field(default_factory=dict)
    additional_vrms: list[Path] = field(default_factory=list)


class ArchiveHandler:
    """Handles extraction and processing of downloaded files."""
    
    def __init__(self, extract_base_dir: Path):
        self.extract_base_dir = extract_base_dir
        self.extract_base_dir.mkdir(parents=True, exist_ok=True)
    
    def process(self, file_path: Path, source: str, model_id: str) -> ProcessedFile:
        """
        Process a downloaded file based on its type.
        
        Args:
            file_path: Path to the downloaded file
            source: Source identifier (e.g., "vroid_hub", "sketchfab")
            model_id: Model identifier from the source
            
        Returns:
            ProcessedFile with primary path, type, size, and notes
        """
        ext = file_path.suffix.lower()
        
        if ext == ".vrm":
            return self._process_vrm(file_path)
        elif ext == ".zip":
            extract_dir = self.extract_base_dir / source / model_id
            return self._process_zip(file_path, extract_dir)
        elif ext == ".glb":
            return self._process_glb(file_path)
        else:
            return self._process_unknown(file_path)
    
    def _process_vrm(self, file_path: Path) -> ProcessedFile:
        """Process a direct VRM file."""
        return ProcessedFile(
            primary_path=file_path,
            file_type="vrm",
            size_bytes=file_path.stat().st_size,
            notes={},
        )
    
    def _process_zip(self, zip_path: Path, extract_dir: Path) -> ProcessedFile:
        """
        Extract ZIP archive and detect VRM files and metadata.
        
        Preserves the original archive and extracts to a dedicated folder.
        """
        extract_dir.mkdir(parents=True, exist_ok=True)
        notes: dict = {}
        vrm_files: list[Path] = []
        all_files: list[str] = []
        
        # Extract all contents
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
            all_files = zf.namelist()
        
        notes["archive_contents"] = all_files
        notes["original_archive"] = str(zip_path)
        
        # Find all VRM files
        vrm_files = list(extract_dir.rglob("*.vrm"))
        
        # Parse metadata files
        metadata = self._parse_metadata_files(extract_dir)
        if metadata:
            notes["parsed_metadata"] = metadata
        
        # Determine primary file and type
        if vrm_files:
            primary_path = vrm_files[0]
            file_type = "vrm"
            additional_vrms = vrm_files[1:] if len(vrm_files) > 1 else []
        else:
            # No VRM found, keep as zip reference
            primary_path = zip_path
            file_type = "zip"
            additional_vrms = []
            
            # Check for GLB files
            glb_files = list(extract_dir.rglob("*.glb"))
            if glb_files:
                notes["glb_files"] = [str(f.relative_to(extract_dir)) for f in glb_files]
                notes["conversion"] = self._get_conversion_notes()
        
        return ProcessedFile(
            primary_path=primary_path,
            file_type=file_type,
            size_bytes=primary_path.stat().st_size,
            notes=notes,
            additional_vrms=additional_vrms,
        )
    
    def _process_glb(self, file_path: Path) -> ProcessedFile:
        """Process a GLB file with conversion instructions."""
        return ProcessedFile(
            primary_path=file_path,
            file_type="glb",
            size_bytes=file_path.stat().st_size,
            notes={"conversion": self._get_conversion_notes()},
        )
    
    def _process_unknown(self, file_path: Path) -> ProcessedFile:
        """Process an unknown file type."""
        ext = file_path.suffix.lower().lstrip(".")
        return ProcessedFile(
            primary_path=file_path,
            file_type=ext or "unknown",
            size_bytes=file_path.stat().st_size,
            notes={},
        )
    
    def _get_conversion_notes(self) -> dict:
        """Get standard conversion instructions for GLB files."""
        return {
            "recommended_tools": [
                "Blender + VRM Add-on for direct VRM export",
                "Unity + UniVRM package",
            ],
            "docs": [
                "https://vrm-addon-for-blender.info/en-us/",
                "https://github.com/vrm-c/UniVRM",
            ],
            "note": "GLB files require manual conversion to VRM format",
        }
    
    def _parse_metadata_files(self, extract_dir: Path) -> dict:
        """Parse metadata from JSON, TXT, and README files in extracted archive."""
        metadata: dict = {}
        
        # Parse JSON files
        for json_file in extract_dir.rglob("*.json"):
            try:
                content = json.loads(json_file.read_text(encoding="utf-8"))
                key = json_file.stem
                metadata[f"json_{key}"] = content
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        
        # Parse TXT and README files
        for pattern in ["*.txt", "README*", "readme*", "LICENSE*", "license*"]:
            for txt_file in extract_dir.rglob(pattern):
                if txt_file.is_file():
                    try:
                        content = txt_file.read_text(encoding="utf-8")
                        # Truncate long files
                        if len(content) > 2000:
                            content = content[:2000] + "\n... [truncated]"
                        key = txt_file.name.replace(".", "_")
                        metadata[f"text_{key}"] = content
                    except UnicodeDecodeError:
                        pass
        
        return metadata
