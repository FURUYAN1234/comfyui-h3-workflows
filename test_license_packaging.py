"""License/distribution regressions; standard library only, no model imports."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_release
from verify_package import (
    APACHE_LICENSE, APACHE_SHA256, LIGHTX2V_FILES, PLAGUEKIND,
    ROOT, verify_license_notices,
)


class LicensePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'source'
        self.source.mkdir()
        files = [APACHE_LICENSE, PLAGUEKIND / 'LICENSE',
                 PLAGUEKIND / 'THIRD_PARTY_NOTICES.md',
                 Path('LICENSES_AND_NOTICES.md'), Path('VERSION.json')]
        files += [PLAGUEKIND / name for name in LIGHTX2V_FILES]
        for relative in files:
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.destination = self.folder / 'candidate.zip'

    def build(self, update=True):
        with patch.object(build_release, 'ROOT', self.source):
            build_release.build(self.destination, update=update)

    def assert_rejected_before_packaging(self, pattern):
        with self.assertRaisesRegex(AssertionError, pattern):
            verify_license_notices(self.source)
        with self.assertRaisesRegex(AssertionError, pattern):
            self.build()
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.source / 'SHA256SUMS.json').exists())

    def test_complete_license_and_existing_notices_pass(self):
        verify_license_notices(self.source)

    def test_missing_apache_body_is_rejected_before_manifest_update(self):
        (self.source / APACHE_LICENSE).unlink()
        self.assert_rejected_before_packaging('Missing Apache-2.0')

    def test_truncated_apache_body_is_rejected(self):
        target = self.source / APACHE_LICENSE
        target.write_bytes(target.read_bytes()[:1000])
        self.assert_rejected_before_packaging('incomplete or modified')

    def test_changed_apache_terms_are_rejected(self):
        target = self.source / APACHE_LICENSE
        target.write_bytes(target.read_bytes().replace(b'perpetual', b'temporary', 1))
        self.assert_rejected_before_packaging('incomplete or modified')

    def test_windows_line_endings_are_accepted(self):
        target = self.source / APACHE_LICENSE
        target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))
        verify_license_notices(self.source)

    def test_missing_each_file_mapping_is_rejected(self):
        for document in ('LICENSES_AND_NOTICES.md', PLAGUEKIND / 'THIRD_PARTY_NOTICES.md'):
            target = self.source / document
            original = target.read_bytes()
            for local, (upstream, _, _) in LIGHTX2V_FILES.items():
                for name in (local, upstream):
                    with self.subTest(document=str(document), mapping=name):
                        target.write_bytes(original.replace(name.encode(), b'removed.py'))
                        self.assert_rejected_before_packaging('Missing LightX2V file mapping')
                        target.write_bytes(original)

    def test_missing_node_local_scope_notice_is_rejected(self):
        (self.source / PLAGUEKIND / 'THIRD_PARTY_NOTICES.md').unlink()
        self.assert_rejected_before_packaging('Missing license scope notice')

    def test_missing_mit_author_notice_is_rejected(self):
        target = self.source / PLAGUEKIND / 'LICENSE'
        target.write_bytes(target.read_bytes().replace(b'Copyright (c) 2026 PlagueKind', b''))
        self.assert_rejected_before_packaging('PlagueKind MIT notice was removed')

    def test_missing_source_attribution_and_change_notices_are_rejected(self):
        for local, (_, attribution, changes) in LIGHTX2V_FILES.items():
            target = self.source / PLAGUEKIND / local
            original = target.read_bytes()
            for notice in (attribution, changes):
                with self.subTest(file=local, notice=notice):
                    target.write_bytes(original.replace(notice.encode(), b'removed'))
                    self.assert_rejected_before_packaging('Missing SLA attribution/change notice')
                    target.write_bytes(original)

    def test_existing_archive_and_manifest_are_never_overwritten(self):
        self.destination.write_bytes(b'existing original archive')
        manifest = self.source / 'SHA256SUMS.json'
        manifest.write_bytes(b'existing manifest')
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(self.destination.read_bytes(), b'existing original archive')
        self.assertEqual(manifest.read_bytes(), b'existing manifest')

    def test_new_archive_contains_full_license_and_matching_manifest(self):
        self.build()
        extracted = self.folder / 'extracted'
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIsNone(archive.testzip())
            archive.extractall(extracted)
        root = extracted / json.loads((self.source / 'VERSION.json').read_text())['archive_root']
        verify_license_notices(root)
        self.assertEqual(hashlib.sha256((root / APACHE_LICENSE).read_bytes()).hexdigest(), APACHE_SHA256)
        manifest = json.loads((root / 'SHA256SUMS.json').read_text())
        self.assertEqual(list(manifest), sorted(manifest), 'Manifest order must be platform-independent')
        self.assertIn(APACHE_LICENSE.as_posix(), manifest)
        self.assertIn((PLAGUEKIND / 'THIRD_PARTY_NOTICES.md').as_posix(), manifest)
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)

    def test_manifest_change_requires_explicit_refresh(self):
        self.build()
        self.destination = self.folder / 'second-candidate.zip'
        (self.source / 'extra.txt').write_text('new payload', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Manifest is stale'):
            self.build(update=False)
        self.assertFalse(self.destination.exists())

    def test_fresh_rebuild_is_byte_identical(self):
        self.build()
        first = self.destination.read_bytes()
        self.destination = self.folder / 'rebuilt-candidate.zip'
        self.build(update=False)
        self.assertEqual(self.destination.read_bytes(), first)


if __name__ == '__main__':
    unittest.main()
