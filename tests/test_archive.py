"""Property-based tests for ArchiveHandler."""
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from archive import ArchiveHandler, ProcessedFile


# Strategies for generating test data
filename_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=20
)

file_content_strategy = st.binary(min_size=1, max_size=1000)

metadata_strategy = st.dictionaries(
    keys=st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
    values=st.text(max_size=50),
    max_size=5
)


@st.composite
def zip_contents_strategy(draw):
    """Generate random ZIP file contents."""
    num_files = draw(st.integers(min_value=1, max_value=5))
    files = {}
    for i in range(num_files):
        name = draw(filename_strategy) + f"_{i}.dat"
        content = draw(file_content_strategy)
        files[name] = content
    return files


@st.composite
def zip_with_vrm_strategy(draw):
    """Generate ZIP contents that include VRM files."""
    num_vrms = draw(st.integers(min_value=1, max_value=3))
    num_other = draw(st.integers(min_value=0, max_value=3))
    files = {}
    
    # Add VRM files
    for i in range(num_vrms):
        name = draw(filename_strategy) + f"_{i}.vrm"
        content = draw(file_content_strategy)
        files[name] = content
    
    # Add other files
    for i in range(num_other):
        name = draw(filename_strategy) + f"_{i}.dat"
        content = draw(file_content_strategy)
        files[name] = content
    
    return files


@st.composite
def zip_with_metadata_strategy(draw):
    """Generate ZIP contents with metadata files."""
    files = {}
    
    # Add a VRM file
    vrm_name = draw(filename_strategy) + ".vrm"
    files[vrm_name] = draw(file_content_strategy)
    
    # Add JSON metadata
    if draw(st.booleans()):
        metadata = draw(metadata_strategy)
        files["metadata.json"] = json.dumps(metadata).encode("utf-8")
    
    # Add README
    if draw(st.booleans()):
        readme_content = draw(st.text(min_size=1, max_size=200))
        files["README.txt"] = readme_content.encode("utf-8")
    
    return files


def create_test_zip(tmpdir: Path, contents: dict[str, bytes]) -> Path:
    """Create a test ZIP file with given contents."""
    zip_path = tmpdir / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, content in contents.items():
            zf.writestr(name, content)
    return zip_path


class TestZipExtractionCompleteness:
    """
    Feature: vrm-auto-scraper, Property 4: ZIP Extraction Completeness
    Validates: Requirements 3.1
    
    For any valid ZIP archive, extracting it SHALL produce a directory containing
    all files that were in the original archive, with matching file names and sizes.
    """
    
    @given(contents=zip_contents_strategy())
    @settings(max_examples=100)
    def test_zip_extraction_completeness(self, contents: dict[str, bytes]):
        """Property 4: All files in ZIP are extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = create_test_zip(tmpdir, contents)
            extract_base = tmpdir / "extracted"
            
            handler = ArchiveHandler(extract_base)
            result = handler.process(zip_path, "test_source", "test_model")
            
            # Verify archive_contents in notes
            assert "archive_contents" in result.notes
            
            # Verify all files were extracted
            extract_dir = extract_base / "test_source" / "test_model"
            for filename, original_content in contents.items():
                extracted_file = extract_dir / filename
                assert extracted_file.exists(), f"File {filename} not extracted"
                assert extracted_file.read_bytes() == original_content, f"Content mismatch for {filename}"


class TestVrmDetectionInArchives:
    """
    Feature: vrm-auto-scraper, Property 5: VRM Detection in Archives
    Validates: Requirements 3.2
    
    For any ZIP archive containing one or more .vrm files, the archive handler
    SHALL identify all VRM files and mark the first one as the primary model file.
    """
    
    @given(contents=zip_with_vrm_strategy())
    @settings(max_examples=100)
    def test_vrm_detection_in_archives(self, contents: dict[str, bytes]):
        """Property 5: VRM files are detected and first is marked primary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = create_test_zip(tmpdir, contents)
            extract_base = tmpdir / "extracted"
            
            handler = ArchiveHandler(extract_base)
            result = handler.process(zip_path, "test_source", "test_model")
            
            # Count VRM files in original contents
            vrm_names = [n for n in contents.keys() if n.endswith(".vrm")]
            
            # Verify file_type is vrm
            assert result.file_type == "vrm"
            
            # Verify primary_path is a VRM file
            assert result.primary_path.suffix.lower() == ".vrm"
            
            # Verify additional VRMs are tracked if multiple exist
            total_vrms = 1 + len(result.additional_vrms)
            assert total_vrms == len(vrm_names)


class TestMetadataFileParsing:
    """
    Feature: vrm-auto-scraper, Property 6: Metadata File Parsing
    Validates: Requirements 3.3
    
    For any ZIP archive containing metadata files (*.json, *.txt, README*),
    the archive handler SHALL parse these files and include their contents in notes.
    """
    
    @given(contents=zip_with_metadata_strategy())
    @settings(max_examples=100)
    def test_metadata_file_parsing(self, contents: dict[str, bytes]):
        """Property 6: Metadata files are parsed and included in notes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = create_test_zip(tmpdir, contents)
            extract_base = tmpdir / "extracted"
            
            handler = ArchiveHandler(extract_base)
            result = handler.process(zip_path, "test_source", "test_model")
            
            # Check if metadata files exist in contents
            has_json = any(n.endswith(".json") for n in contents.keys())
            has_txt = any(n.endswith(".txt") or n.startswith("README") for n in contents.keys())
            
            if has_json or has_txt:
                assert "parsed_metadata" in result.notes
                parsed = result.notes["parsed_metadata"]
                
                # Verify JSON files are parsed
                if has_json:
                    json_keys = [k for k in parsed.keys() if k.startswith("json_")]
                    assert len(json_keys) > 0
                
                # Verify TXT files are parsed
                if has_txt:
                    txt_keys = [k for k in parsed.keys() if k.startswith("text_")]
                    assert len(txt_keys) > 0


class TestGlbConversionNotes:
    """
    Feature: vrm-auto-scraper, Property 7: GLB Conversion Notes
    Validates: Requirements 3.4
    
    For any GLB file processed by the archive handler, the resulting record
    SHALL contain conversion instructions referencing Blender VRM add-on and Unity UniVRM.
    """
    
    @given(content=file_content_strategy)
    @settings(max_examples=100)
    def test_glb_conversion_notes(self, content: bytes):
        """Property 7: GLB files have conversion instructions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            glb_path = tmpdir / "model.glb"
            glb_path.write_bytes(content)
            extract_base = tmpdir / "extracted"
            
            handler = ArchiveHandler(extract_base)
            result = handler.process(glb_path, "test_source", "test_model")
            
            # Verify file_type is glb
            assert result.file_type == "glb"
            
            # Verify conversion notes exist
            assert "conversion" in result.notes
            conversion = result.notes["conversion"]
            
            # Verify recommended tools include Blender and Unity
            assert "recommended_tools" in conversion
            tools_str = " ".join(conversion["recommended_tools"]).lower()
            assert "blender" in tools_str
            assert "unity" in tools_str or "univrm" in tools_str
            
            # Verify docs are included
            assert "docs" in conversion
            assert len(conversion["docs"]) > 0


class TestArchivePreservation:
    """
    Feature: vrm-auto-scraper, Property 8: Archive Preservation
    Validates: Requirements 3.5
    
    For any archive that is extracted, the original archive file SHALL still
    exist on disk after extraction completes.
    """
    
    @given(contents=zip_contents_strategy())
    @settings(max_examples=100)
    def test_archive_preservation(self, contents: dict[str, bytes]):
        """Property 8: Original archive is preserved after extraction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = create_test_zip(tmpdir, contents)
            original_size = zip_path.stat().st_size
            extract_base = tmpdir / "extracted"
            
            handler = ArchiveHandler(extract_base)
            result = handler.process(zip_path, "test_source", "test_model")
            
            # Verify original archive still exists
            assert zip_path.exists()
            assert zip_path.stat().st_size == original_size
            
            # Verify notes reference original archive
            assert "original_archive" in result.notes
            assert result.notes["original_archive"] == str(zip_path)


from archive import is_skippable, ACCESSORY_KEYWORDS, SKIP_EXTENSIONS


class TestSkipCriteria:
    """
    Feature: vrm-pipeline-simplification, Property 5: Skip Criteria
    Validates: Requirements 3.1, 3.2
    
    For any file path, if it has .pmx extension OR contains accessory keywords
    (props, weapon, accessory, item, clothing, stage), the pipeline SHALL skip it.
    """
    
    @given(filename=filename_strategy)
    @settings(max_examples=20, deadline=None)
    def test_pmx_files_are_skipped(self, filename: str):
        """PMX files are always skipped."""
        path = Path(f"/some/dir/{filename}.pmx")
        should_skip, reason = is_skippable(path)
        assert should_skip is True
        assert reason == "pmx_format"
    
    @given(filename=filename_strategy)
    @settings(max_examples=20, deadline=None)
    def test_pmd_files_are_skipped(self, filename: str):
        """PMD files are always skipped."""
        path = Path(f"/some/dir/{filename}.pmd")
        should_skip, reason = is_skippable(path)
        assert should_skip is True
        assert reason == "pmx_format"
    
    @given(keyword=st.sampled_from(ACCESSORY_KEYWORDS), filename=filename_strategy)
    @settings(max_examples=20, deadline=None)
    def test_accessory_keywords_are_skipped(self, keyword: str, filename: str):
        """Files with accessory keywords in path are skipped."""
        path = Path(f"/some/{keyword}/{filename}.fbx")
        should_skip, reason = is_skippable(path)
        assert should_skip is True
        assert reason.startswith("accessory_keyword:")
    
    @given(filename=filename_strategy)
    @settings(max_examples=20, deadline=None)
    def test_normal_fbx_not_skipped(self, filename: str):
        """Normal FBX files without accessory keywords are not skipped."""
        # Ensure filename doesn't contain any accessory keywords
        clean_filename = filename
        for kw in ACCESSORY_KEYWORDS:
            clean_filename = clean_filename.replace(kw, "model")
        
        path = Path(f"/some/models/{clean_filename}.fbx")
        should_skip, reason = is_skippable(path)
        
        # Only assert not skipped if path truly has no keywords
        path_lower = str(path).lower()
        has_keyword = any(kw in path_lower for kw in ACCESSORY_KEYWORDS)
        if not has_keyword:
            assert should_skip is False
            assert reason == ""
    
    @given(filename=filename_strategy)
    @settings(max_examples=20, deadline=None)
    def test_vrm_files_not_skipped(self, filename: str):
        """VRM files are not skipped (they're already the target format)."""
        # Ensure filename doesn't contain any accessory keywords
        clean_filename = filename
        for kw in ACCESSORY_KEYWORDS:
            clean_filename = clean_filename.replace(kw, "avatar")
        
        path = Path(f"/some/models/{clean_filename}.vrm")
        should_skip, reason = is_skippable(path)
        
        # Only assert not skipped if path truly has no keywords
        path_lower = str(path).lower()
        has_keyword = any(kw in path_lower for kw in ACCESSORY_KEYWORDS)
        if not has_keyword:
            assert should_skip is False
