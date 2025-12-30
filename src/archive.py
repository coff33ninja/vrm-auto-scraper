"""Archive handling for downloaded VRM models and related files."""
import json
import logging
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from classifier import ItemClassifier

logger = logging.getLogger(__name__)

# Common 7-Zip installation paths on Windows
SEVEN_ZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def find_7zip() -> str | None:
    """Find 7-Zip executable on the system."""
    # Check if 7z is in PATH
    if shutil.which("7z"):
        return "7z"
    
    # Check common installation paths
    for path in SEVEN_ZIP_PATHS:
        if Path(path).exists():
            return path
    
    return None


SEVEN_ZIP_PATH = find_7zip()


# Keywords that indicate a file is an accessory (not a full avatar model)
ACCESSORY_KEYWORDS = [
    "accessory", "accessories", "props", "prop",
    "weapon", "weapons", "item", "items",
    "clothing", "clothes", "outfit", "costume",
    "hair", "wig",  # Standalone hair models
    "stage", "background", "scene", "environment",
    "effect", "effects", "particle",
]

# File extensions that should always be skipped
SKIP_EXTENSIONS = {".pmx", ".pmd"}  # MMD formats - typically accessories

# Global classifier instance (lazy-loaded)
_classifier: "ItemClassifier | None" = None


def get_classifier() -> "ItemClassifier | None":
    """Get or create the global classifier instance."""
    global _classifier
    if _classifier is None:
        try:
            from classifier import ItemClassifier
            from config import config
            _classifier = ItemClassifier(
                db_path=config.db_path,
                clip_threshold=config.clip_threshold,
                text_threshold=config.text_threshold,
                fuzzy_threshold=config.fuzzy_threshold,
                enable_ai=config.enable_ai_classification,
            )
            logger.info("AI classifier initialized")
        except ImportError as e:
            logger.warning(f"AI classifier not available: {e}")
            return None
    return _classifier


def is_skippable(
    file_path: Path,
    thumbnail_path: Path | None = None,
    use_ai: bool = True,
) -> tuple[bool, str]:
    """
    Check if a file should be skipped during conversion.
    
    Uses AI classification if available, falls back to keyword matching.
    
    Args:
        file_path: Path to the file to check
        thumbnail_path: Optional path to thumbnail for AI classification
        use_ai: Whether to use AI classification (default True)
        
    Returns:
        Tuple of (should_skip, reason)
    """
    # Check extension first (always skip PMX/PMD)
    ext = file_path.suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return True, "pmx_format"
    
    # Try AI classification if enabled
    if use_ai:
        classifier = get_classifier()
        if classifier:
            result = classifier.classify(file_path, thumbnail_path)
            if result.should_skip:
                return True, f"ai:{result.category}:{result.confidence:.2f}"
    
    # Fallback to keyword matching
    path_lower = str(file_path).lower()
    for keyword in ACCESSORY_KEYWORDS:
        if keyword in path_lower:
            return True, f"accessory_keyword:{keyword}"
    
    return False, ""


@dataclass
class ProcessedFile:
    """Result of processing a downloaded file."""
    primary_path: Path
    file_type: str  # "vrm", "glb", "zip", "rar", or other extension
    size_bytes: int
    notes: dict = field(default_factory=dict)
    additional_vrms: list[Path] = field(default_factory=list)


class ArchiveHandler:
    """Handles extraction and processing of downloaded files."""
    
    def __init__(self, extract_base_dir: Path):
        self.extract_base_dir = extract_base_dir
        self.extract_base_dir.mkdir(parents=True, exist_ok=True)
        
        if SEVEN_ZIP_PATH:
            logger.info(f"7-Zip found at: {SEVEN_ZIP_PATH}")
        else:
            logger.warning("7-Zip not found. RAR extraction will not work.")
    
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
        extract_dir = self.extract_base_dir / source / model_id
        
        if ext == ".vrm":
            return self._process_vrm(file_path)
        elif ext == ".zip":
            return self._process_archive(file_path, extract_dir, "zip")
        elif ext == ".rar":
            return self._process_archive(file_path, extract_dir, "rar")
        elif ext == ".7z":
            return self._process_archive(file_path, extract_dir, "7z")
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
    
    def _extract_with_7zip(self, archive_path: Path, extract_dir: Path) -> tuple[bool, list[str]]:
        """
        Extract archive using 7-Zip.
        
        Returns:
            Tuple of (success, list of extracted files)
        """
        if not SEVEN_ZIP_PATH:
            return False, []
        
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Run 7z to extract: x = extract with full paths, -o = output dir, -y = yes to all
            result = subprocess.run(
                [SEVEN_ZIP_PATH, "x", str(archive_path), f"-o{extract_dir}", "-y"],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"7-Zip extraction failed: {result.stderr}")
                return False, []
            
            # List extracted files
            extracted_files = []
            for f in extract_dir.rglob("*"):
                if f.is_file():
                    try:
                        rel_path = str(f.relative_to(extract_dir))
                        extracted_files.append(rel_path)
                    except ValueError:
                        pass
            
            return True, extracted_files
            
        except subprocess.TimeoutExpired:
            logger.error(f"7-Zip extraction timed out for {archive_path}")
            return False, []
        except Exception as e:
            logger.error(f"7-Zip extraction error: {e}")
            return False, []
    
    def _extract_with_zipfile(self, zip_path: Path, extract_dir: Path) -> tuple[bool, list[str]]:
        """Extract ZIP using Python's zipfile module as fallback."""
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
                return True, zf.namelist()
        except Exception as e:
            logger.error(f"zipfile extraction error: {e}")
            return False, []
    
    def _process_archive(self, archive_path: Path, extract_dir: Path, archive_type: str) -> ProcessedFile:
        """
        Extract archive (ZIP, RAR, 7z) and detect VRM files and metadata.
        
        Uses 7-Zip for RAR/7z, falls back to Python zipfile for ZIP.
        Preserves the original archive and extracts to a dedicated folder.
        """
        notes: dict = {}
        notes["original_archive"] = str(archive_path)
        notes["archive_type"] = archive_type
        
        # Try extraction
        success = False
        all_files: list[str] = []
        
        if archive_type == "zip" and not SEVEN_ZIP_PATH:
            # Use Python zipfile for ZIP if 7-Zip not available
            success, all_files = self._extract_with_zipfile(archive_path, extract_dir)
        else:
            # Use 7-Zip for all archive types
            success, all_files = self._extract_with_7zip(archive_path, extract_dir)
            
            # Fallback to Python zipfile for ZIP
            if not success and archive_type == "zip":
                success, all_files = self._extract_with_zipfile(archive_path, extract_dir)
        
        if not success:
            error_msg = "7-Zip not installed" if not SEVEN_ZIP_PATH else "Extraction failed"
            return ProcessedFile(
                primary_path=archive_path,
                file_type=archive_type,
                size_bytes=archive_path.stat().st_size,
                notes={"error": f"{error_msg}. Install 7-Zip from https://7-zip.org/"},
            )
        
        notes["archive_contents"] = all_files
        logger.info(f"Extracted {len(all_files)} files from {archive_path.name}")
        
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
            logger.info(f"Found {len(vrm_files)} VRM file(s) in archive")
        else:
            # No VRM found, check for other 3D formats
            primary_path = archive_path
            file_type = archive_type
            additional_vrms = []
            
            # Check for GLB/GLTF files
            glb_files = list(extract_dir.rglob("*.glb")) + list(extract_dir.rglob("*.gltf"))
            if glb_files:
                notes["glb_files"] = [str(f.relative_to(extract_dir)) for f in glb_files]
                notes["conversion"] = self._get_conversion_notes()
                logger.info(f"Found {len(glb_files)} GLB/GLTF file(s) - conversion needed")
            
            # Check for FBX files
            fbx_files = list(extract_dir.rglob("*.fbx"))
            if fbx_files:
                notes["fbx_files"] = [str(f.relative_to(extract_dir)) for f in fbx_files]
                logger.info(f"Found {len(fbx_files)} FBX file(s)")
            
            # Check for OBJ files
            obj_files = list(extract_dir.rglob("*.obj"))
            if obj_files:
                notes["obj_files"] = [str(f.relative_to(extract_dir)) for f in obj_files]
                logger.info(f"Found {len(obj_files)} OBJ file(s)")
            
            # Check for Blender files
            blend_files = list(extract_dir.rglob("*.blend"))
            if blend_files:
                notes["blend_files"] = [str(f.relative_to(extract_dir)) for f in blend_files]
                logger.info(f"Found {len(blend_files)} Blender file(s)")
        
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
