"""Regression checks for launching and upgrading the organized repository."""
import importlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sentinel_app import desktop_backend, paths

ROOT = Path(__file__).resolve().parents[1]


class RepositoryLayoutTests(unittest.TestCase):
    def test_saved_data_path_survives_package_move_and_different_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, '-c',
                'from sentinel_app.desktop_backend import DATA; print(DATA)'],
                cwd=folder, env={**os.environ, 'PYTHONPATH': str(ROOT)},
                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), ROOT / 'desktop-data')
        self.assertEqual(desktop_backend.DATA, ROOT / 'desktop-data')
        self.assertEqual(desktop_backend.BASE, ROOT)

    def test_templates_and_help_guide_resolve_from_repository(self):
        for name in ('azuredeploy.json', 'lighthouse-onboard.json'):
            with self.subTest(name=name):
                document = json.loads((paths.TEMPLATES / name).read_text(encoding='utf-8'))
                self.assertIn('resources', document)
        self.assertTrue((paths.DOCS / 'DESKTOP-START-HERE.md').is_file())

    def test_module_launcher_calls_desktop_main(self):
        with patch('sentinel_app.desktop_app.main') as start:
            runpy.run_module('sentinel_app', run_name='__main__')
        start.assert_called_once_with()

    def test_all_application_modules_import_without_cloud_calls(self):
        modules = [p.stem for p in (ROOT / 'sentinel_app').glob('*.py')
                   if not p.name.startswith('__')]
        with patch('subprocess.run', side_effect=AssertionError('Unexpected CLI call')), \
             patch('subprocess.Popen', side_effect=AssertionError('Unexpected process')):
            for name in modules:
                with self.subTest(module=name):
                    importlib.import_module('sentinel_app.' + name)

    def test_both_onboarding_cli_entry_points(self):
        for module in ('lighthouse_onboarding', 'full_onboarding'):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, '-m', 'sentinel_app.'+module, '--help'],
                                        cwd=ROOT, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--config', result.stdout)
                self.assertIn('--apply', result.stdout)

    def test_relative_documentation_links_exist(self):
        documents = [ROOT / 'README.md', *(ROOT / 'docs').glob('*.md')]
        for document in documents:
            for target in re.findall(r'\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                if re.match(r'\w+:', target) or target.startswith('#'):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target.split('#')[0]).exists())


if __name__ == '__main__':
    unittest.main()
