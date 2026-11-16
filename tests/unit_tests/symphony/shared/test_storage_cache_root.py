"""Unit tests for the per-account cache root in shared.storage."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openjiuwen.symphony.shared.storage import materialize_s3_dir, user_cache_root

_POSIX_ONLY = unittest.skipUnless(os.name == "posix", "POSIX file modes")


class UserCacheRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch.object(tempfile, "tempdir", self._temp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp_root = Path(self._temp.name)

    def test_name_carries_an_account_token(self) -> None:
        root = user_cache_root("s3-dir-cache")
        self.assertNotEqual(root.name, "s3-dir-cache")
        self.assertTrue(root.name.startswith("s3-dir-cache-"))
        self.assertEqual(root.parent, self.temp_root)

    @_POSIX_ONLY
    def test_name_differs_between_accounts(self) -> None:
        first = user_cache_root("s3-dir-cache")
        with mock.patch.object(os, "getuid", return_value=int(first.name.rsplit("-", 1)[1]) + 1):
            second = user_cache_root("s3-dir-cache")
        self.assertNotEqual(first, second)

    @_POSIX_ONLY
    def test_new_directory_is_private(self) -> None:
        root = user_cache_root("s3-dir-cache")
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    @_POSIX_ONLY
    def test_existing_directory_is_tightened(self) -> None:
        # mkdir(exist_ok=True) skips the mode, so an earlier run of the same
        # account can leave a world-readable directory behind.
        stale = self.temp_root / f"s3-dir-cache-{os.getuid()}"
        stale.mkdir(mode=0o777)
        os.chmod(stale, 0o777)
        root = user_cache_root("s3-dir-cache")
        self.assertEqual(root, stale)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    @_POSIX_ONLY
    def test_symlink_is_refused(self) -> None:
        target = self.temp_root / "elsewhere"
        target.mkdir()
        link = self.temp_root / f"s3-dir-cache-{os.getuid()}"
        link.symlink_to(target)
        with self.assertRaises(RuntimeError):
            user_cache_root("s3-dir-cache")


class MaterializeS3DirCacheRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch.object(tempfile, "tempdir", self._temp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp_root = Path(self._temp.name)

    def test_cache_directory_sits_under_the_account_root(self) -> None:
        def fake_download(*, base_uri: str, relative_path: str, destination_path: str | Path) -> bool:
            if relative_path != "manifest.json":
                return False
            Path(destination_path).write_text("{}", encoding="utf-8")
            return True

        with mock.patch(
            "openjiuwen.symphony.shared.storage.download_s3_relative_object_if_exists",
            side_effect=fake_download,
        ):
            local_dir = materialize_s3_dir("s3://bucket/index")

        self.assertFalse((self.temp_root / "s3-dir-cache").exists())
        self.assertEqual(local_dir.parent, user_cache_root("s3-dir-cache"))


if __name__ == "__main__":
    unittest.main()
