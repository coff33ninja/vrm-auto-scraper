"""Property-based tests for converter module."""
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from converter import get_vrm_output_path, vrm_exists_for


# Strategies for generating test data
filename_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=20
)

format_strategy = st.sampled_from([".fbx", ".obj", ".blend", ".glb"])


class TestVRMOutputPath:
    """
    Feature: vrm-pipeline-simplification, Property 2: VRM Output Path Consistency
    Validates: Requirements 2.2, 4.2, 4.3
    
    For any convertible file (FBX, Blend, OBJ, GLB), the output VRM path SHALL be
    the input path with .vrm extension.
    """
    
    @given(filename=filename_strategy, ext=format_strategy)
    @settings(max_examples=20, deadline=None)
    def test_vrm_output_path_has_vrm_extension(self, filename: str, ext: str):
        """Property 2: Output path always has .vrm extension."""
        input_path = Path(f"/some/dir/{filename}{ext}")
        output_path = get_vrm_output_path(input_path)
        
        assert output_path.suffix == ".vrm"
        assert output_path.stem == input_path.stem
        assert output_path.parent == input_path.parent
    
    @given(filename=filename_strategy, ext=format_strategy)
    @settings(max_examples=20, deadline=None)
    def test_vrm_output_path_preserves_directory(self, filename: str, ext: str):
        """Output path is in same directory as input."""
        input_path = Path(f"/deep/nested/path/{filename}{ext}")
        output_path = get_vrm_output_path(input_path)
        
        assert output_path.parent == input_path.parent


class TestVRMExistsCheck:
    """
    Feature: vrm-pipeline-simplification, Property 3: Skip Existing VRM
    Validates: Requirements 2.4
    
    For any convertible file that already has a corresponding .vrm file,
    vrm_exists_for() SHALL return the VRM path.
    """
    
    @given(filename=filename_strategy, ext=format_strategy)
    @settings(max_examples=20, deadline=None)
    def test_vrm_exists_returns_path_when_exists(self, filename: str, ext: str):
        """vrm_exists_for returns VRM path when it exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create input file
            input_path = tmpdir / f"{filename}{ext}"
            input_path.write_bytes(b"dummy content")
            
            # Create corresponding VRM file
            vrm_path = tmpdir / f"{filename}.vrm"
            vrm_path.write_bytes(b"vrm content")
            
            # Check that vrm_exists_for finds it
            result = vrm_exists_for(input_path)
            assert result is not None
            assert result == vrm_path
    
    @given(filename=filename_strategy, ext=format_strategy)
    @settings(max_examples=20, deadline=None)
    def test_vrm_exists_returns_none_when_missing(self, filename: str, ext: str):
        """vrm_exists_for returns None when VRM doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create input file only (no VRM)
            input_path = tmpdir / f"{filename}{ext}"
            input_path.write_bytes(b"dummy content")
            
            # Check that vrm_exists_for returns None
            result = vrm_exists_for(input_path)
            assert result is None
