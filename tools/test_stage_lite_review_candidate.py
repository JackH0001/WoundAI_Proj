"""Minimal Git context, exact packaged bytes and pre-publication failure cases."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from stage_lite_review_candidate import stage, sources, PAYLOAD, MANIFEST, verify

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.output = self.base / 'context'
        for relative in sources().values():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, path)
        (self.repo / '.gitignore').write_text('private/\n')
        self.git('init', '-q')
        self.git('config', 'core.hooksPath', str(self.base / 'no-hooks'))
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'synthetic source')
        self.commit = self.git('rev-parse', 'HEAD').decode().strip()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL)

    def test_clean_stage_copies_only_allowlist_and_binds_commit(self):
        private = self.repo / 'private'
        private.mkdir()
        (private / 'not-for-container.txt').write_text('synthetic excluded fixture')
        before = {p: p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        result = stage(self.repo, self.output)
        self.assertEqual(result['source_commit'], self.commit)
        self.assertEqual(result['files'], 12)
        self.assertEqual({p.relative_to(self.output).as_posix() for p in self.output.rglob('*') if p.is_file()},
                         set(PAYLOAD) | {MANIFEST, 'Dockerfile'})
        self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))

    def test_dirty_or_untracked_source_cannot_be_claimed_as_commit(self):
        for name in ('Backend/LiteReview/runtime.py', 'untracked.txt'):
            with self.subTest(name=name):
                path = self.repo / name
                original = path.read_bytes() if path.exists() else None
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'clean committed'):
                    stage(self.repo, self.output)
                self.assertFalse(self.output.exists())
                if original is None: path.unlink()
                else: path.write_bytes(original)

    def test_assume_unchanged_does_not_bypass_git_byte_comparison(self):
        name = 'Backend/LiteReview/runtime.py'
        self.git('update-index', '--assume-unchanged', name)
        (self.repo / name).write_bytes(b'changed despite clean porcelain')
        self.assertEqual(self.git('status', '--porcelain'), b'')
        with self.assertRaisesRegex(ValueError, 'differs from committed'):
            stage(self.repo, self.output)

    def test_committed_symlink_is_not_followed(self):
        path = self.repo / 'Backend/LiteReview/runtime.py'
        path.unlink()
        path.symlink_to('reader.py')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'unsafe synthetic link')
        with self.assertRaisesRegex(ValueError, 'unsafe source'):
            stage(self.repo, self.output)

    def test_preserves_existing_output_and_rejects_in_repo_output(self):
        self.output.mkdir()
        marker = self.output / 'retain'
        marker.write_bytes(b'original')
        for target in (self.output, self.repo / 'output'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                stage(self.repo, target)
        self.assertEqual(marker.read_bytes(), b'original')

    def test_source_switch_before_publish_keeps_destination_absent(self):
        original = subprocess.check_output
        seen = 0
        def switch(args, *a, **kw):
            nonlocal seen
            if args[-2:] == ['rev-parse', 'HEAD']:
                seen += 1
                if seen == 2: return b'f' * 40 + b'\n'
            return original(args, *a, **kw)
        with patch('stage_lite_review_candidate.subprocess.check_output', side_effect=switch):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                stage(self.repo, self.output)
        self.assertFalse(self.output.exists())

    def test_recipe_or_runtime_change_rejected_after_staging(self):
        stage(self.repo, self.output)
        for name in ('Dockerfile', 'Backend/LiteReview/runtime.py'):
            with self.subTest(name=name):
                path = self.output / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n# changed\n')
                with self.assertRaises(ValueError): verify(self.output, staged=True)
                path.write_bytes(original)

    def test_extra_missing_and_linked_payload_rejected(self):
        stage(self.repo, self.output)
        extra = self.output / 'unexpected.p8'
        extra.write_bytes(b'synthetic fixture only')
        with self.assertRaises(ValueError): verify(self.output, staged=True)
        extra.unlink()
        path = self.output / PAYLOAD[0]
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError): verify(self.output, staged=True)
        path.symlink_to('Dockerfile')
        with self.assertRaises(ValueError): verify(self.output, staged=True)
        path.unlink()
        path.write_bytes(original)

    def test_manifest_cannot_expand_payload_or_hide_build_recipe(self):
        stage(self.repo, self.output)
        path = self.output / MANIFEST
        original = json.loads(path.read_bytes())
        mutations = [dict(source_commit='HEAD'), dict(recipe_sha256='bad'),
                     dict(execution_authorized=True), dict(files={'../outside': {}})]
        for change in mutations:
            with self.subTest(change=change):
                value = copy.deepcopy(original)
                value.update(change)
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError): verify(self.output, staged=True)
        path.write_text(json.dumps(original))

    def test_runtime_requires_exact_payload_without_recipe(self):
        expected = stage(self.repo, self.output)
        with self.assertRaises(ValueError): verify(self.output)
        (self.output / 'Dockerfile').unlink()
        self.assertEqual(verify(self.output), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
