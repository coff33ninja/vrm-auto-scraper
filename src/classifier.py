"""AI-powered item classification for weapon/accessory detection."""
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of AI classification."""
    should_skip: bool
    confidence: float  # 0.0 to 1.0
    category: str | None  # "weapon", "accessory", "prop", etc.
    reason: str  # Human-readable explanation
    strategies_used: list[str]  # ["clip", "text", "fuzzy"]


class FuzzyMatcher:
    """Fuzzy string matching using RapidFuzz for weapon/accessory detection."""
    
    WEAPON_TERMS = [
        "sword", "katana", "blade", "dagger", "knife",
        "gun", "pistol", "rifle", "shotgun", "revolver",
        "axe", "hammer", "mace", "spear", "bow", "arrow",
        "weapon", "armament", "scythe", "staff", "wand",
    ]
    
    ACCESSORY_TERMS = [
        "prop", "accessory", "item", "object",
        "clothing", "outfit", "costume", "dress", "shirt",
        "hair", "wig", "hat", "glasses", "mask",
        "stage", "background", "scene", "room", "environment",
        "effect", "particle", "aura",
    ]
    
    def __init__(self, threshold: int = 80):
        """
        Initialize fuzzy matcher.
        
        Args:
            threshold: Minimum fuzzy match score (0-100) to consider a match
        """
        self.threshold = threshold
        self.weapon_terms = set(self.WEAPON_TERMS)
        self.accessory_terms = set(self.ACCESSORY_TERMS)
        self.all_terms = self.WEAPON_TERMS + self.ACCESSORY_TERMS
    
    def match(self, text: str) -> tuple[str | None, int, str | None]:
        """
        Find best fuzzy match for text against known terms.
        
        Args:
            text: Text to match (filename, path component, etc.)
            
        Returns:
            Tuple of (matched_term, score, category) or (None, 0, None)
            category is "weapon" or "accessory"
        """
        # Normalize text: lowercase, replace separators with spaces
        text_lower = text.lower()
        words = text_lower.replace("_", " ").replace("-", " ").replace(".", " ").split()
        
        best_match: str | None = None
        best_score = 0
        best_category: str | None = None
        
        for word in words:
            if len(word) < 3:  # Skip very short words
                continue
                
            result = process.extractOne(
                word,
                self.all_terms,
                scorer=fuzz.ratio,
            )
            
            if result and result[1] > best_score:
                best_match = result[0]
                best_score = result[1]
                
                # Determine category
                if best_match in self.weapon_terms:
                    best_category = "weapon"
                else:
                    best_category = "accessory"
        
        if best_score >= self.threshold:
            return best_match, best_score, best_category
        
        return None, 0, None
    
    def classify(self, file_path: Path) -> ClassificationResult:
        """
        Classify a file using fuzzy matching on filename and path.
        
        Args:
            file_path: Path to the file
            
        Returns:
            ClassificationResult with fuzzy matching results
        """
        # Check filename
        filename = file_path.stem
        match_term, score, category = self.match(filename)
        
        # Also check parent directories
        if not match_term:
            for part in file_path.parts[:-1]:  # Exclude filename
                match_term, score, category = self.match(part)
                if match_term:
                    break
        
        if match_term:
            confidence = score / 100.0  # Convert to 0-1 range
            return ClassificationResult(
                should_skip=True,
                confidence=confidence,
                category=category,
                reason=f"Fuzzy match: '{match_term}' (score: {score})",
                strategies_used=["fuzzy"],
            )
        
        return ClassificationResult(
            should_skip=False,
            confidence=0.0,
            category=None,
            reason="No fuzzy match found",
            strategies_used=["fuzzy"],
        )


class ClassificationCache:
    """SQLite cache for classification results."""
    
    def __init__(self, db_path: Path):
        """
        Initialize cache with SQLite database.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_table()
    
    def _init_table(self):
        """Create cache table if it doesn't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS classification_cache (
                file_path TEXT PRIMARY KEY,
                file_mtime REAL,
                should_skip INTEGER,
                confidence REAL,
                category TEXT,
                reason TEXT,
                strategies TEXT,
                cached_at TEXT
            )
        """)
        self.conn.commit()
    
    def get(self, file_path: Path) -> ClassificationResult | None:
        """
        Get cached classification result if valid.
        
        Args:
            file_path: Path to the file
            
        Returns:
            ClassificationResult if cached and valid, None otherwise
        """
        try:
            current_mtime = file_path.stat().st_mtime
        except OSError:
            return None
        
        cursor = self.conn.execute(
            "SELECT file_mtime, should_skip, confidence, category, reason, strategies "
            "FROM classification_cache WHERE file_path = ?",
            (str(file_path),)
        )
        row = cursor.fetchone()
        
        if not row:
            return None
        
        cached_mtime, should_skip, confidence, category, reason, strategies = row
        
        # Invalidate if file was modified
        if abs(cached_mtime - current_mtime) > 0.001:
            self.delete(file_path)
            return None
        
        return ClassificationResult(
            should_skip=bool(should_skip),
            confidence=confidence,
            category=category,
            reason=reason,
            strategies_used=strategies.split(",") if strategies else [],
        )
    
    def set(self, file_path: Path, result: ClassificationResult):
        """
        Cache a classification result.
        
        Args:
            file_path: Path to the file
            result: Classification result to cache
        """
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            return  # Can't cache if file doesn't exist
        
        self.conn.execute(
            """
            INSERT OR REPLACE INTO classification_cache 
            (file_path, file_mtime, should_skip, confidence, category, reason, strategies, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(file_path),
                mtime,
                int(result.should_skip),
                result.confidence,
                result.category,
                result.reason,
                ",".join(result.strategies_used),
                datetime.now().isoformat(),
            )
        )
        self.conn.commit()
    
    def delete(self, file_path: Path):
        """Delete cached result for a file."""
        self.conn.execute(
            "DELETE FROM classification_cache WHERE file_path = ?",
            (str(file_path),)
        )
        self.conn.commit()
    
    def clear(self):
        """Clear all cached results."""
        self.conn.execute("DELETE FROM classification_cache")
        self.conn.commit()
    
    def close(self):
        """Close database connection."""
        self.conn.close()



class CLIPClassifier:
    """Zero-shot image classification using CLIP model."""
    
    SKIP_LABELS = [
        "weapon", "sword", "gun", "knife", "axe",
        "prop", "accessory", "item", "object",
        "clothing", "outfit", "costume",
        "stage", "background", "environment",
    ]
    
    AVATAR_LABELS = [
        "character", "avatar", "humanoid", "person",
        "anime character", "3d model", "vtuber model",
    ]
    
    def __init__(self):
        """Initialize CLIP model. Raises error if unavailable."""
        try:
            from transformers import CLIPProcessor, CLIPModel
            from PIL import Image
            self.Image = Image
        except ImportError as e:
            raise ImportError(
                "CLIP classification requires transformers, torch, and Pillow. "
                "Install with: pip install transformers torch Pillow"
            ) from e
        
        logger.info("Loading CLIP model...")
        self.model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        self.processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        self.all_labels = self.SKIP_LABELS + self.AVATAR_LABELS
        logger.info("CLIP model loaded successfully")
    
    def classify_image(self, image_path: Path) -> dict[str, float]:
        """
        Classify image against skip and avatar labels.
        
        Args:
            image_path: Path to image file
            
        Returns:
            Dict mapping label to confidence score (0-1)
        """
        try:
            image = self.Image.open(image_path).convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to load image {image_path}: {e}")
            return {}
        
        inputs = self.processor(
            text=self.all_labels,
            images=image,
            return_tensors="pt",
            padding=True,
        )
        
        outputs = self.model(**inputs)
        logits = outputs.logits_per_image[0]
        probs = logits.softmax(dim=0)
        
        return {label: prob.item() for label, prob in zip(self.all_labels, probs)}
    
    def classify(self, image_path: Path, threshold: float = 0.7) -> ClassificationResult:
        """
        Classify an image and determine if it should be skipped.
        
        Args:
            image_path: Path to image file
            threshold: Confidence threshold for skip decision
            
        Returns:
            ClassificationResult
        """
        scores = self.classify_image(image_path)
        
        if not scores:
            return ClassificationResult(
                should_skip=False,
                confidence=0.0,
                category=None,
                reason="Failed to classify image",
                strategies_used=["clip"],
            )
        
        # Find highest scoring skip label
        skip_scores = {k: v for k, v in scores.items() if k in self.SKIP_LABELS}
        avatar_scores = {k: v for k, v in scores.items() if k in self.AVATAR_LABELS}
        
        max_skip_label = max(skip_scores, key=skip_scores.get) if skip_scores else None
        max_skip_score = skip_scores.get(max_skip_label, 0) if max_skip_label else 0
        
        max_avatar_label = max(avatar_scores, key=avatar_scores.get) if avatar_scores else None
        max_avatar_score = avatar_scores.get(max_avatar_label, 0) if max_avatar_label else 0
        
        # Decide based on which category has higher confidence
        if max_skip_score > max_avatar_score and max_skip_score >= threshold:
            return ClassificationResult(
                should_skip=True,
                confidence=max_skip_score,
                category=max_skip_label,
                reason=f"CLIP: '{max_skip_label}' ({max_skip_score:.2%})",
                strategies_used=["clip"],
            )
        
        return ClassificationResult(
            should_skip=False,
            confidence=max_avatar_score,
            category=max_avatar_label,
            reason=f"CLIP: '{max_avatar_label}' ({max_avatar_score:.2%})",
            strategies_used=["clip"],
        )


class TextClassifier:
    """Zero-shot text classification using transformers pipeline."""
    
    SKIP_LABELS = [
        "weapon", "prop", "accessory", "clothing", "stage", "background",
    ]
    
    AVATAR_LABELS = [
        "character", "avatar", "humanoid",
    ]
    
    def __init__(self):
        """Initialize text classification pipeline. Raises error if unavailable."""
        try:
            from transformers import pipeline
        except ImportError as e:
            raise ImportError(
                "Text classification requires transformers. "
                "Install with: pip install transformers torch"
            ) from e
        
        logger.info("Loading text classification model...")
        self.classifier = pipeline(
            "zero-shot-classification",
            model="facebook/bart-large-mnli",
        )
        self.all_labels = self.SKIP_LABELS + self.AVATAR_LABELS
        logger.info("Text classification model loaded successfully")
    
    def classify_text(self, text: str) -> dict[str, float]:
        """
        Classify text against skip and avatar labels.
        
        Args:
            text: Text to classify (filename, description, etc.)
            
        Returns:
            Dict mapping label to confidence score (0-1)
        """
        result = self.classifier(text, self.all_labels)
        return dict(zip(result["labels"], result["scores"]))
    
    def classify(self, text: str, threshold: float = 0.6) -> ClassificationResult:
        """
        Classify text and determine if it should be skipped.
        
        Args:
            text: Text to classify
            threshold: Confidence threshold for skip decision
            
        Returns:
            ClassificationResult
        """
        scores = self.classify_text(text)
        
        # Find highest scoring skip label
        skip_scores = {k: v for k, v in scores.items() if k in self.SKIP_LABELS}
        avatar_scores = {k: v for k, v in scores.items() if k in self.AVATAR_LABELS}
        
        max_skip_label = max(skip_scores, key=skip_scores.get) if skip_scores else None
        max_skip_score = skip_scores.get(max_skip_label, 0) if max_skip_label else 0
        
        max_avatar_label = max(avatar_scores, key=avatar_scores.get) if avatar_scores else None
        max_avatar_score = avatar_scores.get(max_avatar_label, 0) if max_avatar_label else 0
        
        if max_skip_score > max_avatar_score and max_skip_score >= threshold:
            return ClassificationResult(
                should_skip=True,
                confidence=max_skip_score,
                category=max_skip_label,
                reason=f"Text: '{max_skip_label}' ({max_skip_score:.2%})",
                strategies_used=["text"],
            )
        
        return ClassificationResult(
            should_skip=False,
            confidence=max_avatar_score,
            category=max_avatar_label,
            reason=f"Text: '{max_avatar_label}' ({max_avatar_score:.2%})",
            strategies_used=["text"],
        )



class ItemClassifier:
    """
    Multi-strategy classifier combining CLIP, text NLP, and fuzzy matching.
    
    Orchestrates all classification strategies and combines their results.
    """
    
    def __init__(
        self,
        db_path: Path,
        clip_threshold: float = 0.7,
        text_threshold: float = 0.6,
        fuzzy_threshold: int = 80,
        enable_ai: bool = True,
    ):
        """
        Initialize the item classifier.
        
        Args:
            db_path: Path to SQLite database for caching
            clip_threshold: Confidence threshold for CLIP classification
            text_threshold: Confidence threshold for text classification
            fuzzy_threshold: Score threshold for fuzzy matching (0-100)
            enable_ai: Whether to use AI classifiers (CLIP, text)
        """
        self.cache = ClassificationCache(db_path)
        self.fuzzy_matcher = FuzzyMatcher(fuzzy_threshold)
        self.clip_threshold = clip_threshold
        self.text_threshold = text_threshold
        self.enable_ai = enable_ai
        
        # Lazy-load AI classifiers
        self._clip_classifier: CLIPClassifier | None = None
        self._text_classifier: TextClassifier | None = None
        
        if enable_ai:
            self._init_ai_classifiers()
    
    def _init_ai_classifiers(self):
        """Initialize AI classifiers. Raises error if unavailable."""
        try:
            self._clip_classifier = CLIPClassifier()
        except ImportError as e:
            raise ImportError(f"CLIP classifier initialization failed: {e}") from e
        
        try:
            self._text_classifier = TextClassifier()
        except ImportError as e:
            raise ImportError(f"Text classifier initialization failed: {e}") from e
    
    def classify(
        self,
        file_path: Path,
        thumbnail_path: Path | None = None,
    ) -> ClassificationResult:
        """
        Classify an item using all available strategies.
        
        Strategy order:
        1. Check cache first
        2. CLIP on thumbnail (if available)
        3. Text classification on filename
        4. Fuzzy matching on filename/path
        5. Combine results
        
        Args:
            file_path: Path to the 3D model file
            thumbnail_path: Optional path to thumbnail image
            
        Returns:
            ClassificationResult with combined analysis
        """
        # Check cache first
        cached = self.cache.get(file_path)
        if cached:
            logger.debug(f"Cache hit for {file_path.name}")
            return cached
        
        strategies_used: list[str] = []
        results: list[ClassificationResult] = []
        
        # 1. CLIP classification on thumbnail
        if self.enable_ai and thumbnail_path and thumbnail_path.exists() and self._clip_classifier:
            clip_result = self._clip_classifier.classify(thumbnail_path, self.clip_threshold)
            results.append(clip_result)
            strategies_used.append("clip")
            
            # If CLIP is highly confident, use it directly
            if clip_result.should_skip and clip_result.confidence >= self.clip_threshold:
                result = ClassificationResult(
                    should_skip=True,
                    confidence=clip_result.confidence,
                    category=clip_result.category,
                    reason=clip_result.reason,
                    strategies_used=strategies_used,
                )
                self.cache.set(file_path, result)
                return result
        
        # 2. Text classification on filename
        if self.enable_ai and self._text_classifier:
            filename = file_path.stem
            text_result = self._text_classifier.classify(filename, self.text_threshold)
            results.append(text_result)
            strategies_used.append("text")
            
            if text_result.should_skip and text_result.confidence >= self.text_threshold:
                result = ClassificationResult(
                    should_skip=True,
                    confidence=text_result.confidence,
                    category=text_result.category,
                    reason=text_result.reason,
                    strategies_used=strategies_used,
                )
                self.cache.set(file_path, result)
                return result
        
        # 3. Fuzzy matching (always available)
        fuzzy_result = self.fuzzy_matcher.classify(file_path)
        results.append(fuzzy_result)
        strategies_used.append("fuzzy")
        
        if fuzzy_result.should_skip:
            result = ClassificationResult(
                should_skip=True,
                confidence=fuzzy_result.confidence,
                category=fuzzy_result.category,
                reason=fuzzy_result.reason,
                strategies_used=strategies_used,
            )
            self.cache.set(file_path, result)
            return result
        
        # 4. Combine results - no skip detected
        max_confidence = max((r.confidence for r in results), default=0.0)
        
        result = ClassificationResult(
            should_skip=False,
            confidence=min(max(max_confidence, 0.0), 1.0),  # Clamp to [0, 1]
            category=None,
            reason="No skip criteria matched",
            strategies_used=strategies_used,
        )
        
        self.cache.set(file_path, result)
        return result
    
    def close(self):
        """Close database connection."""
        self.cache.close()



def check_ai_dependencies() -> dict[str, bool]:
    """
    Check if AI dependencies are available.
    
    Returns:
        Dict with availability status for each dependency
    """
    status = {
        "transformers": False,
        "torch": False,
        "pillow": False,
        "rapidfuzz": True,  # Already imported at module level
    }
    
    try:
        import transformers
        status["transformers"] = True
    except ImportError:
        pass
    
    try:
        import torch
        status["torch"] = True
    except ImportError:
        pass
    
    try:
        from PIL import Image
        status["pillow"] = True
    except ImportError:
        pass
    
    return status


def require_ai_dependencies():
    """
    Verify all AI dependencies are installed.
    
    Raises:
        ImportError: If any required dependency is missing
    """
    status = check_ai_dependencies()
    
    missing = [name for name, available in status.items() if not available]
    
    if missing:
        raise ImportError(
            f"Missing required AI dependencies: {', '.join(missing)}. "
            f"Install with: pip install transformers torch Pillow rapidfuzz"
        )
    
    logger.info("All AI dependencies available")


def get_classifier(
    db_path: Path,
    enable_ai: bool = True,
    clip_threshold: float = 0.7,
    text_threshold: float = 0.6,
    fuzzy_threshold: int = 80,
) -> ItemClassifier:
    """
    Factory function to create an ItemClassifier.
    
    Args:
        db_path: Path to SQLite database
        enable_ai: Whether to enable AI classification
        clip_threshold: CLIP confidence threshold
        text_threshold: Text classification threshold
        fuzzy_threshold: Fuzzy matching threshold
        
    Returns:
        Configured ItemClassifier instance
        
    Raises:
        ImportError: If enable_ai=True but dependencies missing
    """
    if enable_ai:
        require_ai_dependencies()
    
    return ItemClassifier(
        db_path=db_path,
        clip_threshold=clip_threshold,
        text_threshold=text_threshold,
        fuzzy_threshold=fuzzy_threshold,
        enable_ai=enable_ai,
    )
