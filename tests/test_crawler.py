"""Property-based tests for CrawlerEngine."""
import tempfile
from pathlib import Path
from typing import Iterator
from unittest.mock import Mock, patch

import pytest
from hypothesis import given, settings, strategies as st

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from archive import ArchiveHandler
from crawler import CrawlerEngine, CrawlResult
from sources.base import BaseSource, ModelInfo
from storage import MetadataStore, ModelRecord


class MockSource(BaseSource):
    """Mock source for testing."""
    
    def __init__(self, name: str, models: list[ModelInfo]):
        self._name = name
        self._models = models
        self.download_calls = []
    
    def get_source_name(self) -> str:
        return self._name
    
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        for model in self._models[:max_results]:
            yield model
    
    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        self.download_calls.append(model.source_model_id)
        # Create a fake VRM file
        output_path = output_dir / f"{model.source_model_id}.vrm"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake vrm content")
        return output_path


class FailingSource(BaseSource):
    """Source that fails on specific models."""
    
    def __init__(self, name: str, models: list[ModelInfo], fail_ids: set[str]):
        self._name = name
        self._models = models
        self._fail_ids = fail_ids
    
    def get_source_name(self) -> str:
        return self._name
    
    def search(self, keywords: list[str], max_results: int) -> Iterator[ModelInfo]:
        for model in self._models[:max_results]:
            yield model
    
    def download(self, model: ModelInfo, output_dir: Path) -> Path:
        if model.source_model_id in self._fail_ids:
            raise Exception(f"Simulated failure for {model.source_model_id}")
        
        output_path = output_dir / f"{model.source_model_id}.vrm"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake vrm content")
        return output_path


# Strategies
model_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20
)


@st.composite
def model_info_strategy(draw):
    """Generate a random ModelInfo."""
    model_id = draw(model_id_strategy)
    return ModelInfo(
        source_model_id=model_id,
        name=f"Model {model_id}",
        artist="Test Artist",
        source_url=f"https://example.com/{model_id}",
        is_downloadable=True,
        license="Test License",
        license_url="https://example.com/license",
    )


class TestDuplicatePrevention:
    """
    Feature: vrm-auto-scraper, Property 2: Duplicate Prevention (Idempotence)
    Validates: Requirements 2.5
    
    For any model that already exists in the database, re-crawling SHALL NOT
    create duplicate entries.
    """
    
    @given(models=st.lists(model_info_strategy(), min_size=1, max_size=5, unique_by=lambda m: m.source_model_id))
    @settings(max_examples=50)
    def test_duplicate_prevention(self, models: list[ModelInfo]):
        """Property 2: Re-crawling doesn't create duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            raw_dir = tmpdir / "raw"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            archive_handler = ArchiveHandler(extract_dir)
            source = MockSource("test_source", models)
            
            crawler = CrawlerEngine(
                sources=[source],
                store=store,
                archive_handler=archive_handler,
                raw_dir=raw_dir,
            )
            
            # First crawl
            result1 = crawler.crawl(max_per_source=len(models))
            count_after_first = store.count()
            
            # Second crawl (should skip all)
            result2 = crawler.crawl(max_per_source=len(models))
            count_after_second = store.count()
            
            # Verify no duplicates
            assert count_after_first == count_after_second
            assert result2.downloaded == 0
            assert result2.skipped == len(models)
            
            store.close()


class TestErrorResilience:
    """
    Feature: vrm-auto-scraper, Property 3: Error Resilience
    Validates: Requirements 2.6
    
    For any sequence of models being downloaded where one or more downloads fail,
    the crawler SHALL continue processing remaining models.
    """
    
    @given(
        models=st.lists(model_info_strategy(), min_size=3, max_size=6, unique_by=lambda m: m.source_model_id),
        fail_indices=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=2, unique=True)
    )
    @settings(max_examples=50)
    def test_error_resilience(self, models: list[ModelInfo], fail_indices: list[int]):
        """Property 3: Failures don't stop the crawler."""
        # Ensure fail_indices are within bounds
        fail_indices = [i for i in fail_indices if i < len(models)]
        if not fail_indices:
            fail_indices = [0]
        
        fail_ids = {models[i].source_model_id for i in fail_indices}
        expected_success = len(models) - len(fail_ids)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            raw_dir = tmpdir / "raw"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            archive_handler = ArchiveHandler(extract_dir)
            source = FailingSource("test_source", models, fail_ids)
            
            crawler = CrawlerEngine(
                sources=[source],
                store=store,
                archive_handler=archive_handler,
                raw_dir=raw_dir,
            )
            
            result = crawler.crawl(max_per_source=len(models))
            
            # Verify successful downloads match expected
            assert result.downloaded == expected_success
            assert result.failed == len(fail_ids)
            assert store.count() == expected_success
            
            # Verify errors were logged
            assert len(result.errors) == len(fail_ids)
            
            store.close()


class TestMetadataCompleteness:
    """
    Feature: vrm-auto-scraper, Property 10: Metadata Completeness
    Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5
    
    For any model record stored in the database, required fields SHALL be
    non-null and valid.
    """
    
    @given(models=st.lists(model_info_strategy(), min_size=1, max_size=3, unique_by=lambda m: m.source_model_id))
    @settings(max_examples=50)
    def test_metadata_completeness(self, models: list[ModelInfo]):
        """Property 10: All required metadata fields are populated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / "test.db"
            raw_dir = tmpdir / "raw"
            extract_dir = tmpdir / "extracted"
            
            store = MetadataStore(db_path)
            archive_handler = ArchiveHandler(extract_dir)
            source = MockSource("test_source", models)
            
            crawler = CrawlerEngine(
                sources=[source],
                store=store,
                archive_handler=archive_handler,
                raw_dir=raw_dir,
            )
            
            crawler.crawl(max_per_source=len(models))
            
            # Verify all records have complete metadata
            records = store.list_all()
            
            for record in records:
                # Required fields must be non-null
                assert record.source is not None and record.source != ""
                assert record.source_model_id is not None and record.source_model_id != ""
                assert record.name is not None and record.name != ""
                assert record.source_url is not None and record.source_url != ""
                assert record.acquired_at is not None and record.acquired_at != ""
                assert record.file_path is not None and record.file_path != ""
                assert record.file_type is not None and record.file_type != ""
                assert record.size_bytes is not None and record.size_bytes > 0
                
                # Verify file exists
                assert Path(record.file_path).exists()
                
                # Verify size matches actual file
                actual_size = Path(record.file_path).stat().st_size
                assert record.size_bytes == actual_size
                
                # Verify acquired_at is valid ISO timestamp
                from datetime import datetime
                datetime.fromisoformat(record.acquired_at.replace("Z", "+00:00"))
            
            store.close()
