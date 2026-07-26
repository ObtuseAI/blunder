from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blunder.repository_intelligence import build_repository_index, query_repository


class BlunderRepositoryIntelligenceTests(unittest.TestCase):
    def test_index_maps_symbols_imports_tests_and_reuses_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "blunder" / "missions" / "engine.py"
            test = root / "tests" / "test_engine.py"
            engine.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True)
            engine.write_text("def synthesize_mission_graph():\n    return 'graph'\n", encoding="utf-8")
            test.write_text(
                "from blunder.missions.engine import synthesize_mission_graph\n",
                encoding="utf-8",
            )
            first = build_repository_index(root, True)
            second = build_repository_index(root, True)
            ranked = query_repository(first, "synthesize mission graph", 5)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["counts"]["files"], 2)
        self.assertEqual(ranked[0]["path"], "blunder/missions/engine.py")
        self.assertIn("synthesize_mission_graph", ranked[0]["symbols"])


if __name__ == "__main__":
    unittest.main()
