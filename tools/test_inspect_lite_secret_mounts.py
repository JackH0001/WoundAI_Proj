import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from inspect_lite_secret_mounts import inspect


class MountMetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.role = self.root/'oauth'; self.role.mkdir()
    def test_regular_read_only_metadata_never_opens_payload(self):
        (self.role/'key').write_text('synthetic-only')
        with patch.object(Path, 'open', side_effect=AssertionError('payload forbidden')):
            result = inspect(self.root, 'oauth', 'key')
        self.assertEqual(result['file_kind'], '-')
        self.assertTrue(result['contained']); self.assertTrue(result['target_regular'])
    def test_contained_symlink_is_reported_not_automatically_accepted(self):
        (self.role/'target').write_text('synthetic-only'); (self.role/'key').symlink_to('target')
        result = inspect(self.root, 'oauth', 'key')
        self.assertEqual(result['file_kind'], 'l'); self.assertTrue(result['contained'])
    def test_escape_link_is_not_followed_for_target_metadata(self):
        (self.root/'outside').write_text('synthetic-only'); (self.role/'key').symlink_to('../outside')
        result = inspect(self.root, 'oauth', 'key')
        self.assertFalse(result['contained']); self.assertNotIn('target_regular',result)
    def test_linked_mount_directory_is_refused(self):
        (self.root/'api').symlink_to('oauth')
        result = inspect(self.root, 'api', 'key')
        self.assertEqual(result['directory_kind'], 'l'); self.assertNotIn('file_kind', result)
    def test_missing_file_is_reported_without_payload(self):
        self.assertEqual(inspect(self.root,'oauth','key')['error_type'],'FileNotFoundError')
    def test_unexpected_roles_and_traversal_are_refused(self):
        for role, leaf in [('unknown','key'),('oauth','../key'),('oauth','.'),('oauth','')]:
            with self.subTest(role=role,leaf=leaf), self.assertRaises(ValueError):
                inspect(self.root,role,leaf)


if __name__ == '__main__': unittest.main()
