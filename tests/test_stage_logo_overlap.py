"""Focused tests for Stage logo overlaps over the existing content clock."""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "core/stages/export.py"
TEXT = SOURCE.read_text()
TREE = ast.parse(TEXT)
FUNC = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_within_content_logo_graph")
scope = {
    "_target_size": lambda platform: (1920, 1080),
    "_personal_logo_path": lambda: "logo.png",
    "_watermark_path": lambda: None,
    "_has_real_alpha": lambda asset: True,
    "_logo_matte": lambda asset: "black",
}
exec(compile(ast.Module(body=[FUNC], type_ignores=[]), str(SOURCE), "exec"), scope)
build = scope["_within_content_logo_graph"]


class StageLogoOverlapTests(unittest.TestCase):
    def test_intro_overlaps_picture_for_more_than_two_seconds(self):
        graph = build("[0:v]null[base]", "base", "youtube", 3.5, {"offset": 0, "total": 100})
        self.assertIn("fade=t=out:st=1.25:d=1.15:alpha=1", graph)
        self.assertIn("enable='lt(t,2.400000)'", graph)
        self.assertIn("format=rgba", graph)

    def test_outro_starts_inside_content_with_longer_fade(self):
        graph = build("[0:v]null[base]", "base", "youtube", 3.0, {"offset": 98, "total": 100})
        self.assertIn("fade=t=in:st=97.600000:d=1.15:alpha=1", graph)
        self.assertIn("enable='gte(t,0.000000)'", graph)
        self.assertIn("fade=t=out:st=99.750000:d=0.25:alpha=1", graph)

    def test_logo_not_added_in_middle_segments(self):
        graph = build("[0:v]null[base]", "base", "youtube", 5.0, {"offset": 30, "total": 100})
        self.assertNotIn("[brand_intro]", graph)
        self.assertNotIn("[brand_outro]", graph)

    def test_selection_of_logo_segments_uses_longer_window(self):
        self.assertIn("elapsed < 2.4 or elapsed + length > total_duration - 2.4", TEXT)


if __name__ == "__main__":
    unittest.main()
