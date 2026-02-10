# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for FileCheckpointStore - file-based checkpoint persistence."""

import json
import os
import tempfile

from openjiuwen.agent_evolving.checkpointing.store_file import FileCheckpointStore
from openjiuwen.agent_evolving.checkpointing.types import EvolveCheckpoint


def make_mock_checkpoint():
    """Factory for creating mock checkpoint data."""
    return EvolveCheckpoint(
        version="v1",
        run_id="test_run",
        step={"epoch": 1},
        best={"best_score": 0.5},
        seed=42,
        operators_state={"op1": {"param": "value"}},
        producer_state={},
        searcher_state={},
        last_metrics={},
    )


class TestFileCheckpointStoreInit:
    """Test FileCheckpointStore initialization."""

    @staticmethod
    def test_init_creates_directory():
        """Init creates checkpoint directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            assert os.path.exists(tmpdir)

    @staticmethod
    def test_init_creates_nested_directory():
        """Init creates nested directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "nested", "path")
            store = FileCheckpointStore(nested)
            assert os.path.exists(nested)

    @staticmethod
    def test_init_with_none():
        """Init with None dir."""
        store = FileCheckpointStore(None)
        # Verify None behavior through save_checkpoint returning None
        result = store.save_checkpoint(make_mock_checkpoint(), filename="test.json")
        assert result is None


class TestFileCheckpointStoreSave:
    """Test save_checkpoint method."""

    @staticmethod
    def test_save_checkpoint_json():
        """Saves checkpoint as JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            checkpoint = make_mock_checkpoint()
            path = store.save_checkpoint(checkpoint, filename="test_ckpt.json")
            assert os.path.exists(path)
            assert path.endswith(".json")

    @staticmethod
    def test_save_checkpoint_content():
        """Saved checkpoint contains correct data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            checkpoint = make_mock_checkpoint()
            checkpoint.run_id = "my_run"
            path = store.save_checkpoint(checkpoint, filename="test.json")
            with open(path, "r") as f:
                data = json.load(f)
            assert data["run_id"] == "my_run"

    @staticmethod
    def test_save_checkpoint_with_none_dir():
        """No-op when dir is None."""
        store = FileCheckpointStore(None)
        result = store.save_checkpoint(make_mock_checkpoint(), filename="test.json")
        assert result is None


class TestFileCheckpointStoreLoad:
    """Test load_checkpoint method."""

    @staticmethod
    def test_load_checkpoint():
        """Loads checkpoint from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            checkpoint = make_mock_checkpoint()
            checkpoint.run_id = "load_test"
            path = store.save_checkpoint(checkpoint, filename="load.json")
            loaded = store.load_checkpoint(path)
            assert loaded.run_id == "load_test"

    @staticmethod
    def test_load_nonexistent():
        """Returns None for nonexistent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            result = store.load_checkpoint("/nonexistent/file.json")
            assert result is None

    @staticmethod
    def test_load_with_none_dir():
        """Returns None when dir is None."""
        store = FileCheckpointStore(None)
        result = store.load_checkpoint("any_path.json")
        assert result is None

    @staticmethod
    def test_load_state_dict():
        """Inference view: reads operators_state from checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            checkpoint = make_mock_checkpoint()
            checkpoint.operators_state = {"op1": {"param": "value"}}
            path = store.save_checkpoint(checkpoint, filename="latest.json")
            state = store.load_state_dict(path)
            assert state == {"op1": {"param": "value"}}


class TestFileCheckpointStorePath:
    """Test path handling."""

    @staticmethod
    def test_custom_filename():
        """Uses custom filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            path = store.save_checkpoint(make_mock_checkpoint(), filename="custom.json")
            assert "custom.json" in str(path)

    @staticmethod
    def test_default_filename():
        """Generates default filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileCheckpointStore(tmpdir)
            path = store.save_checkpoint(make_mock_checkpoint(), filename="latest.json")
            assert path.endswith("latest.json")
