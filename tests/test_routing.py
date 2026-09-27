"""skill/routing.json: its shape, and agreement with the routing table in skill/SKILL.md."""
import json
import re
import unittest

from helpers import REPO

ROUTING = REPO / "skill" / "routing.json"
SKILL_MD = REPO / "skill" / "SKILL.md"


def routing_table(text: str) -> str:
    """The table rows under the SKILL.md heading that names model routing."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(r"^##\s+.*[Rr]outing", line))
    rows = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            rows.append(line)
    return "\n".join(rows)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.routing = json.loads(ROUTING.read_text())

    def test_every_tier_model_is_in_the_skill_routing_table(self):
        table = routing_table(SKILL_MD.read_text())
        self.assertTrue(table, "no routing table found in SKILL.md")
        cells = set(re.findall(r"`([^`]+)`", table))
        for harness in ("claude", "codex"):
            for tier, entry in self.routing[harness]["tiers"].items():
                with self.subTest(harness=harness, tier=tier):
                    self.assertIn(entry["model"], cells)

    def test_shape(self):
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                h = self.routing[harness]
                self.assertEqual(sorted(h["tiers"]), ["1", "2", "3"])
                for entry in [*h["tiers"].values(), h["adversary"]] + ([h["utility"]] if h["utility"] else []):
                    self.assertEqual(sorted(entry), ["effort", "model"])
                    self.assertTrue(entry["model"])
                self.assertIn(h["reviewer_default_tier"], (1, 2, 3))
        self.assertIsNone(self.routing["codex"]["utility"])
        self.assertTrue(set(self.routing["risky_tags"]) <= set(self.routing["risk_tags"]))
        self.assertIsInstance(self.routing["rework_rounds"], int)
        self.assertGreater(self.routing["rework_rounds"], 0)
        self.assertTrue(self.routing["adversary_variations"])
        self.assertTrue(all(isinstance(v, str) and v for v in self.routing["adversary_variations"]))

    def test_table_extraction_stops_at_the_next_heading(self):
        text = "## Model routing\n| a | `m1` |\n\nprose `m3`\n## Next\n| b | `m2` |\n"
        self.assertEqual(routing_table(text), "| a | `m1` |")


if __name__ == "__main__":
    unittest.main()
