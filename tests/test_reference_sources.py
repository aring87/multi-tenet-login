"""Catch paste/indentation damage without executing uploaded scripts."""
import ast
from pathlib import Path
import unittest

class ReferenceSyntaxTests(unittest.TestCase):
    def test_uploaded_python_parses(self):
        root = Path(__file__).resolve().parents[1] / "detection-as-code"
        for path in root.rglob("*.py"):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
