"""Property-based tests for MetadataStore."""
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from storage import MetadataStore, ModelRecord


# Strategies for generating test data
source_strategy = st.sampled_from(["vroid_hub", "sketchfab", "github"])
model_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50
)
name_strategy = st.text(min_size=1, max_size=100)
url_strategy = st.text(min_size=5, max_size=200).map(lambda s: f"https://example.com/{s}")
timestamp_strategy = st.text(
    alphabet="0123456789-T:Z",
    min_size=20,
    max_size=30
).map(lambda _: "2025-12-30T10:30:00Z")
file_type_strategy = st.sampled_from(["vrm", "glb", "zip"])
size_strategy = st.integers(min_value=1, max_value=100_000_000)
notes_strategy = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.text(max_size=100),
    max_size=5
)


@st.composite
def model_record_strategy(draw):
    """Generate a random ModelRecord."""
    return ModelRecord(
        source=draw(source_strategy),
        source_model_id=draw(model_id_strategy),
        name=draw(name_strategy),
        artist=draw(st.text(max_size=50)),
        source_url=draw(url_strategy),
        license=draw(st.one_of(st.none(), st.text(max_size=50))),
        license_url=draw(st.one_of(st.none(), url_strategy)),
        acquired_at=draw(timestamp_strategy),
        file_path=draw(st.text(min_size=1, max_size=100)),
        file_type=draw(file_type_strategy),
        size_bytes=draw(size_strategy),
        notes=draw(notes_strategy),
    )


class TestPersistenceRoundTrip:
    """
    Feature: vrm-auto-scraper, Property 12: Persistence Round-Trip
    Validates: Requirements 4.7
    
    For any model record added to the store, closing and reopening the store
    SHALL allow retrieval of the same record with all fields intact.
    """
    
    @given(record=model_record_strategy())
    @settings(max_examples=20, deadline=None)
    def test_persistence_round_trip(self, record: ModelRecord):
        """Property 12: Records survive store close/reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            
            # Add record to store
            store1 = MetadataStore(db_path)
            record_id = store1.add(record)
            store1.close()
            
            # Close and reopen store (new instance)
            store2 = MetadataStore(db_path)
            retrieved = store2.get(record_id)
            store2.close()
            
            # Verify all fields match
            assert retrieved is not None
            assert retrieved.source == record.source
            assert retrieved.source_model_id == record.source_model_id
            assert retrieved.name == record.name
            assert retrieved.artist == record.artist
            assert retrieved.source_url == record.source_url
            assert retrieved.license == record.license
            assert retrieved.license_url == record.license_url
            assert retrieved.acquired_at == record.acquired_at
            assert retrieved.file_path == record.file_path
            assert retrieved.file_type == record.file_type
            assert retrieved.size_bytes == record.size_bytes
            assert retrieved.notes == record.notes


class TestExportImportRoundTrip:
    """
    Feature: vrm-auto-scraper, Property 13: Export/Import Round-Trip
    Validates: Requirements 5.2, 5.3, 5.5
    
    For any set of model records in the database, exporting to JSON and then
    importing into an empty database SHALL produce an identical set of records.
    """
    
    @given(records=st.lists(model_record_strategy(), min_size=1, max_size=5, unique_by=lambda r: (r.source, r.source_model_id)))
    @settings(max_examples=20, deadline=None)
    def test_export_import_round_trip(self, records: list[ModelRecord]):
        """Property 13: Export then import preserves all records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path1 = Path(tmpdir) / "source.db"
            db_path2 = Path(tmpdir) / "target.db"
            json_path = Path(tmpdir) / "export.json"
            
            # Add records to source store
            store1 = MetadataStore(db_path1)
            for record in records:
                store1.add(record)
            
            # Export to JSON
            store1.export_json(json_path)
            store1.close()
            
            # Import into fresh store
            store2 = MetadataStore(db_path2)
            imported_count = store2.import_json(json_path)
            
            # Verify counts match
            assert imported_count == len(records)
            assert store2.count() == len(records)
            
            # Verify all records present with matching fields
            imported_records = store2.list_all()
            store2.close()
            
            original_by_key = {(r.source, r.source_model_id): r for r in records}
            
            for imported in imported_records:
                key = (imported.source, imported.source_model_id)
                assert key in original_by_key
                original = original_by_key[key]
                
                assert imported.name == original.name
                assert imported.artist == original.artist
                assert imported.source_url == original.source_url
                assert imported.license == original.license
                assert imported.license_url == original.license_url
                assert imported.acquired_at == original.acquired_at
                assert imported.file_path == original.file_path
                assert imported.file_type == original.file_type
                assert imported.size_bytes == original.size_bytes
                assert imported.notes == original.notes


class TestDuplicatePrevention:
    """
    Feature: vrm-auto-scraper, Property 2: Duplicate Prevention (Idempotence)
    Validates: Requirements 2.5
    
    For any model that already exists in the database, re-adding SHALL NOT
    create duplicate entries.
    """
    
    @given(record=model_record_strategy())
    @settings(max_examples=20, deadline=None)
    def test_exists_check(self, record: ModelRecord):
        """exists() returns True for added records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = MetadataStore(db_path)
            
            # Initially doesn't exist
            assert not store.exists(record.source, record.source_model_id)
            
            # Add record
            store.add(record)
            
            # Now exists
            assert store.exists(record.source, record.source_model_id)
            
            store.close()


from storage import DownloadRecord, DownloadsTracker


# Strategies for download records
status_strategy = st.sampled_from(["downloaded", "extracted", "converted", "failed"])


@st.composite
def download_record_strategy(draw):
    """Generate a random DownloadRecord."""
    return DownloadRecord(
        source=draw(source_strategy),
        source_model_id=draw(model_id_strategy),
        source_url=draw(url_strategy),
        downloaded_at=draw(timestamp_strategy),
        raw_path=draw(st.text(min_size=1, max_size=100)),
        status=draw(status_strategy),
        error=draw(st.one_of(st.none(), st.text(max_size=200))),
    )


class TestDownloadDeduplication:
    """
    Feature: vrm-pipeline-simplification, Property 7: Download Deduplication
    Validates: Requirements 5.2
    
    For any source and model_id combination that exists in the downloads table,
    exists() SHALL return True, preventing re-download.
    """
    
    @given(record=download_record_strategy())
    @settings(max_examples=20, deadline=None)
    def test_download_exists_after_add(self, record: DownloadRecord):
        """Property 7: exists() returns True for added downloads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            tracker = DownloadsTracker(db_path)
            
            # Initially doesn't exist
            assert not tracker.exists(record.source, record.source_model_id)
            
            # Add download record
            tracker.add(record)
            
            # Now exists - should prevent re-download
            assert tracker.exists(record.source, record.source_model_id)
            
            tracker.close()
    
    @given(records=st.lists(download_record_strategy(), min_size=2, max_size=5, unique_by=lambda r: (r.source, r.source_model_id)))
    @settings(max_examples=20, deadline=None)
    def test_multiple_downloads_tracked(self, records: list[DownloadRecord]):
        """Multiple downloads are all tracked independently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            tracker = DownloadsTracker(db_path)
            
            # Add all records
            for record in records:
                tracker.add(record)
            
            # All should exist
            for record in records:
                assert tracker.exists(record.source, record.source_model_id)
            
            # Count should match
            assert tracker.count() == len(records)
            
            tracker.close()
    
    @given(record=download_record_strategy(), new_status=status_strategy)
    @settings(max_examples=20, deadline=None)
    def test_status_update(self, record: DownloadRecord, new_status: str):
        """Status can be updated for existing downloads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            tracker = DownloadsTracker(db_path)
            
            # Add record
            tracker.add(record)
            
            # Update status
            updated = tracker.update_status(record.source, record.source_model_id, new_status)
            assert updated
            
            # Verify status changed
            retrieved = tracker.get(record.source, record.source_model_id)
            assert retrieved is not None
            assert retrieved.status == new_status
            
            tracker.close()
