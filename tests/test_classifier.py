"""Tests for AI-powered item classification."""
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings, strategies as st, HealthCheck

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier import ClassificationCache, ClassificationResult, FuzzyMatcher, ItemClassifier


class TestFuzzyMatcher:
    """Tests for FuzzyMatcher class."""
    
    def test_exact_weapon_match(self):
        """Test exact match for weapon terms."""
        matcher = FuzzyMatcher(threshold=80)
        term, score, category = matcher.match("sword")
        assert term == "sword"
        assert score == 100
        assert category == "weapon"
    
    def test_exact_accessory_match(self):
        """Test exact match for accessory terms."""
        matcher = FuzzyMatcher(threshold=80)
        term, score, category = matcher.match("clothing")
        assert term == "clothing"
        assert score == 100
        assert category == "accessory"
    
    def test_fuzzy_weapon_typo(self):
        """Test fuzzy match catches common typos."""
        matcher = FuzzyMatcher(threshold=80)
        # "sward" is close to "sword"
        term, score, category = matcher.match("sward")
        assert term == "sword"
        assert score >= 80
        assert category == "weapon"
    
    def test_no_match_below_threshold(self):
        """Test that low-similarity strings don't match."""
        matcher = FuzzyMatcher(threshold=80)
        term, score, category = matcher.match("character_model")
        # "character" shouldn't match any weapon/accessory term well
        assert term is None or score < 80
    
    def test_classify_weapon_filename(self):
        """Test classification of weapon filename."""
        matcher = FuzzyMatcher(threshold=80)
        result = matcher.classify(Path("/models/katana_blade_v2.fbx"))
        assert result.should_skip is True
        assert result.category == "weapon"
        assert "fuzzy" in result.strategies_used
    
    def test_classify_accessory_path(self):
        """Test classification catches accessory in path."""
        matcher = FuzzyMatcher(threshold=80)
        result = matcher.classify(Path("/models/props/chair.fbx"))
        assert result.should_skip is True
        assert result.category == "accessory"
    
    def test_classify_avatar_no_skip(self):
        """Test that avatar models are not skipped."""
        matcher = FuzzyMatcher(threshold=80)
        result = matcher.classify(Path("/models/anime_girl_v1.vrm"))
        assert result.should_skip is False
    
    # Property-based tests
    @given(st.sampled_from(FuzzyMatcher.WEAPON_TERMS))
    @settings(max_examples=20)
    def test_property_exact_weapon_terms_match(self, weapon_term: str):
        """Property 5: All exact weapon terms should match with score 100."""
        matcher = FuzzyMatcher(threshold=80)
        term, score, category = matcher.match(weapon_term)
        assert term == weapon_term
        assert score == 100
        assert category == "weapon"
    
    @given(st.sampled_from(FuzzyMatcher.ACCESSORY_TERMS))
    @settings(max_examples=20)
    def test_property_exact_accessory_terms_match(self, accessory_term: str):
        """Property 5: All exact accessory terms should match with score 100."""
        matcher = FuzzyMatcher(threshold=80)
        term, score, category = matcher.match(accessory_term)
        assert term == accessory_term
        assert score == 100
        assert category == "accessory"
    
    @given(
        st.sampled_from([t for t in FuzzyMatcher.WEAPON_TERMS if len(t) >= 5]),
        st.integers(min_value=1, max_value=2),
    )
    @settings(max_examples=20)
    def test_property_typo_weapon_still_matches(self, weapon_term: str, pos_offset: int):
        """Property 5: Weapon terms with 1-char typo should still match."""
        # Create typo by changing one character (not first char)
        pos = min(pos_offset, len(weapon_term) - 1)
        typo_char = 'x' if weapon_term[pos] != 'x' else 'y'
        typo_term = weapon_term[:pos] + typo_char + weapon_term[pos+1:]
        
        matcher = FuzzyMatcher(threshold=60)
        term, score, category = matcher.match(typo_term)
        
        # Should still find a match
        assert term is not None
        assert score >= 60


class TestClassificationCache:
    """Tests for ClassificationCache class."""
    
    def test_cache_set_and_get(self, tmp_path: Path):
        """Test basic cache set and get."""
        db_path = tmp_path / "test.db"
        cache = ClassificationCache(db_path)
        
        test_file = tmp_path / "test.fbx"
        test_file.write_text("test content")
        
        result = ClassificationResult(
            should_skip=True,
            confidence=0.85,
            category="weapon",
            reason="Test reason",
            strategies_used=["fuzzy", "clip"],
        )
        
        cache.set(test_file, result)
        cached = cache.get(test_file)
        
        assert cached is not None
        assert cached.should_skip == result.should_skip
        assert cached.confidence == result.confidence
        assert cached.category == result.category
        assert cached.reason == result.reason
        assert cached.strategies_used == result.strategies_used
        
        cache.close()
    
    def test_cache_invalidation_on_modify(self, tmp_path: Path):
        """Test cache is invalidated when file is modified."""
        import time
        
        db_path = tmp_path / "test.db"
        cache = ClassificationCache(db_path)
        
        test_file = tmp_path / "test.fbx"
        test_file.write_text("original content")
        
        result = ClassificationResult(
            should_skip=True,
            confidence=0.9,
            category="weapon",
            reason="Test",
            strategies_used=["fuzzy"],
        )
        
        cache.set(test_file, result)
        assert cache.get(test_file) is not None
        
        time.sleep(0.1)
        test_file.write_text("modified content")
        
        assert cache.get(test_file) is None
        
        cache.close()
    
    def test_cache_miss_nonexistent_file(self, tmp_path: Path):
        """Test cache returns None for non-cached file."""
        db_path = tmp_path / "test.db"
        cache = ClassificationCache(db_path)
        
        result = cache.get(tmp_path / "nonexistent.fbx")
        assert result is None
        
        cache.close()
    
    @given(
        st.booleans(),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        st.sampled_from(["weapon", "accessory", "prop", None]),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_cache_roundtrip(
        self,
        should_skip: bool,
        confidence: float,
        category: str | None,
    ):
        """Property 2: Cache roundtrip preserves all fields."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            db_path = tmp_path / "test.db"
            cache = ClassificationCache(db_path)
            
            test_file = tmp_path / "test.fbx"
            test_file.write_text("test")
            
            original = ClassificationResult(
                should_skip=should_skip,
                confidence=confidence,
                category=category,
                reason="Test reason",
                strategies_used=["fuzzy"],
            )
            
            cache.set(test_file, original)
            cached = cache.get(test_file)
            
            assert cached is not None
            assert cached.should_skip == original.should_skip
            assert abs(cached.confidence - original.confidence) < 0.0001
            assert cached.category == original.category
            
            cache.close()



class TestItemClassifier:
    """Tests for ItemClassifier orchestrator."""
    
    def test_classifier_without_ai(self, tmp_path: Path):
        """Test classifier works with AI disabled (fuzzy only)."""
        db_path = tmp_path / "test.db"
        classifier = ItemClassifier(db_path, enable_ai=False)
        
        # Create test file
        test_file = tmp_path / "katana_sword.fbx"
        test_file.write_text("test")
        
        result = classifier.classify(test_file)
        
        assert result.should_skip is True
        assert result.category == "weapon"
        assert "fuzzy" in result.strategies_used
        assert 0.0 <= result.confidence <= 1.0
        
        classifier.close()
    
    def test_classifier_avatar_not_skipped(self, tmp_path: Path):
        """Test that avatar files are not skipped."""
        db_path = tmp_path / "test.db"
        classifier = ItemClassifier(db_path, enable_ai=False)
        
        test_file = tmp_path / "anime_girl_v1.vrm"
        test_file.write_text("test")
        
        result = classifier.classify(test_file)
        
        assert result.should_skip is False
        assert 0.0 <= result.confidence <= 1.0
        
        classifier.close()
    
    def test_classifier_caches_results(self, tmp_path: Path):
        """Test that results are cached."""
        db_path = tmp_path / "test.db"
        classifier = ItemClassifier(db_path, enable_ai=False)
        
        test_file = tmp_path / "sword_model.fbx"
        test_file.write_text("test")
        
        # First call
        result1 = classifier.classify(test_file)
        # Second call should hit cache
        result2 = classifier.classify(test_file)
        
        assert result1.should_skip == result2.should_skip
        assert result1.confidence == result2.confidence
        assert result1.category == result2.category
        
        classifier.close()
    
    # Property-based tests
    @given(st.sampled_from(FuzzyMatcher.WEAPON_TERMS + FuzzyMatcher.ACCESSORY_TERMS))
    @settings(max_examples=20)
    def test_property_confidence_bounds(self, term: str):
        """Property 1: Confidence is always between 0.0 and 1.0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            db_path = tmp_path / "test.db"
            classifier = ItemClassifier(db_path, enable_ai=False)
            
            test_file = tmp_path / f"{term}_model.fbx"
            test_file.write_text("test")
            
            result = classifier.classify(test_file)
            
            assert 0.0 <= result.confidence <= 1.0
            
            classifier.close()
    
    @given(st.text(min_size=3, max_size=20, alphabet=st.characters(whitelist_categories=('L',))))
    @settings(max_examples=20)
    def test_property_confidence_bounds_random_names(self, name: str):
        """Property 1: Confidence bounds hold for any filename."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            db_path = tmp_path / "test.db"
            classifier = ItemClassifier(db_path, enable_ai=False)
            
            test_file = tmp_path / f"{name}.fbx"
            test_file.write_text("test")
            
            result = classifier.classify(test_file)
            
            assert 0.0 <= result.confidence <= 1.0
            assert isinstance(result.should_skip, bool)
            assert isinstance(result.strategies_used, list)
            
            classifier.close()
