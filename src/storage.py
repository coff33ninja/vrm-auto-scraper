"""SQLite-based metadata storage for downloaded VRM models."""
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ModelRecord:
    """Represents a downloaded model's metadata."""
    source: str
    source_model_id: str
    name: str
    source_url: str
    acquired_at: str
    file_path: str
    file_type: str
    size_bytes: int
    artist: str = ""
    license: Optional[str] = None
    license_url: Optional[str] = None
    thumbnail_path: Optional[str] = None
    notes: Optional[dict] = None
    id: Optional[int] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        if d["notes"] is None:
            d["notes"] = {}
        return d
    
    @classmethod
    def from_dict(cls, data: dict) -> "ModelRecord":
        """Create ModelRecord from dictionary."""
        return cls(
            id=data.get("id"),
            source=data["source"],
            source_model_id=data["source_model_id"],
            name=data["name"],
            artist=data.get("artist", ""),
            source_url=data["source_url"],
            license=data.get("license"),
            license_url=data.get("license_url"),
            thumbnail_path=data.get("thumbnail_path"),
            acquired_at=data["acquired_at"],
            file_path=data["file_path"],
            file_type=data["file_type"],
            size_bytes=data["size_bytes"],
            notes=data.get("notes") or {},
        )


class MetadataStore:
    """SQLite-based storage for model metadata."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path)
        return self._connection
    
    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = self._conn()
        con.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_model_id TEXT NOT NULL,
                name TEXT NOT NULL,
                artist TEXT,
                source_url TEXT NOT NULL,
                license TEXT,
                license_url TEXT,
                thumbnail_path TEXT,
                acquired_at TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                notes TEXT,
                UNIQUE(source, source_model_id)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_source ON models(source)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_file_type ON models(file_type)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_acquired_at ON models(acquired_at)")
        # Add thumbnail_path column if it doesn't exist (migration)
        try:
            con.execute("ALTER TABLE models ADD COLUMN thumbnail_path TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        con.commit()
    
    def add(self, record: ModelRecord) -> int:
        """Insert a model record. Returns the record ID."""
        con = self._conn()
        cursor = con.execute("""
            INSERT INTO models
            (source, source_model_id, name, artist, source_url,
             license, license_url, thumbnail_path, acquired_at, file_path,
             file_type, size_bytes, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.source,
            record.source_model_id,
            record.name,
            record.artist,
            record.source_url,
            record.license,
            record.license_url,
            record.thumbnail_path,
            record.acquired_at,
            record.file_path,
            record.file_type,
            record.size_bytes,
            json.dumps(record.notes or {}),
        ))
        con.commit()
        return cursor.lastrowid
    
    def exists(self, source: str, source_model_id: str) -> bool:
        """Check if a model already exists in the database."""
        con = self._conn()
        cursor = con.execute(
            "SELECT 1 FROM models WHERE source = ? AND source_model_id = ?",
            (source, source_model_id)
        )
        return cursor.fetchone() is not None
    
    def get(self, record_id: int) -> Optional[ModelRecord]:
        """Get a model record by ID."""
        con = self._conn()
        con.row_factory = sqlite3.Row
        cursor = con.execute("SELECT * FROM models WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        con.row_factory = None
        if row:
            return self._row_to_record(dict(row))
        return None
    
    def list_all(self) -> list[ModelRecord]:
        """Return all model records."""
        con = self._conn()
        con.row_factory = sqlite3.Row
        cursor = con.execute("SELECT * FROM models ORDER BY id DESC")
        results = [self._row_to_record(dict(row)) for row in cursor.fetchall()]
        con.row_factory = None
        return results
    
    def count(self) -> int:
        """Return total number of records."""
        con = self._conn()
        cursor = con.execute("SELECT COUNT(*) FROM models")
        return cursor.fetchone()[0]
    
    def _row_to_record(self, row: dict) -> ModelRecord:
        """Convert a database row to ModelRecord."""
        notes = row.get("notes")
        if notes:
            notes = json.loads(notes)
        return ModelRecord(
            id=row["id"],
            source=row["source"],
            source_model_id=row["source_model_id"],
            name=row["name"],
            artist=row["artist"] or "",
            source_url=row["source_url"],
            license=row["license"],
            license_url=row["license_url"],
            thumbnail_path=row.get("thumbnail_path"),
            acquired_at=row["acquired_at"],
            file_path=row["file_path"],
            file_type=row["file_type"],
            size_bytes=row["size_bytes"],
            notes=notes or {},
        )

    def export_json(self, path: Path) -> None:
        """Export all records to a JSON file."""
        records = self.list_all()
        data = [r.to_dict() for r in records]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    def import_json(self, path: Path) -> int:
        """Import records from a JSON file. Returns count of imported records."""
        data = json.loads(path.read_text(encoding="utf-8"))
        count = 0
        for item in data:
            # Remove id to let database assign new one
            item.pop("id", None)
            record = ModelRecord.from_dict(item)
            try:
                self.add(record)
                count += 1
            except sqlite3.IntegrityError:
                # Skip duplicates
                pass
        return count
    
    def delete(self, record_id: int) -> bool:
        """Delete a record by ID. Returns True if deleted."""
        con = self._conn()
        cursor = con.execute("DELETE FROM models WHERE id = ?", (record_id,))
        con.commit()
        return cursor.rowcount > 0
    
    def clear(self) -> int:
        """Delete all records. Returns count of deleted records."""
        con = self._conn()
        cursor = con.execute("DELETE FROM models")
        con.commit()
        return cursor.rowcount
