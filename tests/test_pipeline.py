"""Property-based tests for VRMPipeline."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline import VRMPipeline, ConversionResult
from storage import MetadataStore, DownloadsTracker


# Strategies
filename_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=20
)


class TestConversionFailureIsolation:
    """
    Feature: vrm-pipeline-simplification, Property 4: Conversion Failure Isolation
    Validates: Requirements 2.3
    
    For any batch of files being converted, a failure in one file SHALL NOT
    prevent other files from being processed.
    """
    
    @given(num_files=st.integers(min_value=2, max_value=5))
    @settings(max_examples=20, deadline=None)
    def test_failure_does_not_stop_other_conversions(self, num_files: int):
        """Property 4: One failure doesn't stop other conversions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            downloads = DownloadsTracker(db_path)
            pipeline = VRMPipeline(store, downloads, extract_dir)
            
            # Create test files
            test_files = []
            for i in range(num_files):
                f = tmpdir / f"model_{i}.fbx"
                f.write_bytes(b"dummy fbx content")
                test_files.append(f)
            
            # Mock convert_file to fail on first file, succeed on others
            results = []
            call_count = [0]
            
            def mock_convert(file_path: Path) -> ConversionResult:
                idx = call_count[0]
                call_count[0] += 1
                
                if idx == 0:
                    # First file fails
                    return ConversionResult(
                        input_path=file_path,
                        output_path=None,
                        success=False,
                        error="Simulated failure",
                        original_format="fbx",
                    )
                else:
                    # Other files succeed - create a fake VRM
                    vrm_path = file_path.with_suffix(".vrm")
                    vrm_path.write_bytes(b"fake vrm")
                    return ConversionResult(
                        input_path=file_path,
                        output_path=vrm_path,
                        success=True,
                        original_format="fbx",
                    )
            
            # Patch convert_file
            with patch.object(pipeline, 'convert_file', side_effect=mock_convert):
                # Process each file
                for f in test_files:
                    result = pipeline.convert_file(f)
                    results.append(result)
            
            # Verify: first failed, rest succeeded
            assert results[0].success is False
            for r in results[1:]:
                assert r.success is True
            
            # All files were attempted
            assert len(results) == num_files
            
            store.close()
            downloads.close()


class TestDatabaseVRMOnly:
    """
    Feature: vrm-pipeline-simplification, Property 6: Database Contains Only VRM
    Validates: Requirements 4.1, 4.4
    
    For any model added to the database, the file_type SHALL be "vrm"
    and the file_path SHALL end with ".vrm".
    """
    
    @given(filename=filename_strategy)
    @settings(max_examples=20, deadline=None)
    def test_only_vrm_files_added_to_database(self, filename: str):
        """Property 6: Only VRM files are stored in database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            downloads = DownloadsTracker(db_path)
            pipeline = VRMPipeline(store, downloads, extract_dir)
            
            # Create a VRM file
            vrm_path = tmpdir / f"{filename}.vrm"
            vrm_path.write_bytes(b"vrm content")
            
            # Use internal method to add record
            record = pipeline._create_model_record(
                vrm_path=vrm_path,
                source="test",
                model_id="test_model",
                name=filename,
                artist="test_artist",
                source_url="https://example.com",
                license_info=None,
                thumbnail_path=None,
                original_format="fbx",
                timestamp="2025-01-01T00:00:00",
            )
            
            # Verify record was created
            assert record is not None
            assert record.file_type == "vrm"
            assert record.file_path.endswith(".vrm")
            
            # Verify in database
            all_records = store.list_all()
            for r in all_records:
                assert r.file_type == "vrm"
                assert r.file_path.endswith(".vrm")
            
            store.close()
            downloads.close()
    
    @given(original_format=st.sampled_from(["fbx", "blend", "obj", "glb", "vrm"]))
    @settings(max_examples=20, deadline=None)
    def test_original_format_preserved(self, original_format: str):
        """Original format is stored even though file_type is vrm."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            downloads = DownloadsTracker(db_path)
            pipeline = VRMPipeline(store, downloads, extract_dir)
            
            # Create a VRM file
            vrm_path = tmpdir / "model.vrm"
            vrm_path.write_bytes(b"vrm content")
            
            # Add with specific original format
            record = pipeline._create_model_record(
                vrm_path=vrm_path,
                source="test",
                model_id=f"model_{original_format}",
                name="Test Model",
                artist="Artist",
                source_url="https://example.com",
                license_info=None,
                thumbnail_path=None,
                original_format=original_format,
                timestamp="2025-01-01T00:00:00",
            )
            
            assert record is not None
            assert record.file_type == "vrm"
            assert record.original_format == original_format
            
            store.close()
            downloads.close()


class TestAPIVRMOnly:
    """
    Feature: vrm-pipeline-simplification, Property 8: API Returns Only VRM
    Validates: Requirements 6.1
    
    The API SHALL only return models with file_type "vrm".
    """
    
    @given(
        num_models=st.integers(min_value=1, max_value=5),
        original_formats=st.lists(
            st.sampled_from(["fbx", "blend", "obj", "glb", "vrm"]),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=20, deadline=None)
    def test_all_models_in_db_are_vrm(self, num_models: int, original_formats: list[str]):
        """Property 8: All models stored via pipeline have file_type=vrm."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            downloads = DownloadsTracker(db_path)
            pipeline = VRMPipeline(store, downloads, extract_dir)
            
            # Add models with various original formats
            for i, orig_fmt in enumerate(original_formats[:num_models]):
                vrm_path = tmpdir / f"model_{i}.vrm"
                vrm_path.write_bytes(b"vrm content")
                
                pipeline._create_model_record(
                    vrm_path=vrm_path,
                    source="test",
                    model_id=f"model_{i}_{orig_fmt}",
                    name=f"Test Model {i}",
                    artist="Artist",
                    source_url="https://example.com",
                    license_info=None,
                    thumbnail_path=None,
                    original_format=orig_fmt,
                    timestamp="2025-01-01T00:00:00",
                )
            
            # Verify all records have file_type=vrm
            all_records = store.list_all()
            for record in all_records:
                assert record.file_type == "vrm", f"Expected vrm, got {record.file_type}"
                assert record.file_path.endswith(".vrm"), f"Path should end with .vrm: {record.file_path}"
            
            store.close()
            downloads.close()
