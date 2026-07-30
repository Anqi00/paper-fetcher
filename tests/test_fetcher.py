import json
import tempfile
import unittest
from pathlib import Path

from feedback import (
    build_dislike_profile,
    dislike_similarity,
    ensure_dislike_file,
    load_dislikes,
)
from fetcher import deduplicate, save_json, save_markdown, select_digest


def paper(identifier, published="2026-01-01", source="arXiv", abstract=""):
    return {
        "id": identifier,
        "arxiv_id": identifier,
        "doi": "",
        "source": source,
        "title": identifier,
        "authors": [],
        "abstract": abstract,
        "published": published,
        "url": f"https://arxiv.org/abs/{identifier}",
        "pdf_url": None,
        "venue": "cs.CV",
    }


class FetcherTests(unittest.TestCase):
    def test_deduplicate_keeps_richer_record(self):
        short = paper("1234.5678", abstract="short")
        rich = paper("1234.5678", abstract="a much longer abstract")
        self.assertEqual(deduplicate([short, rich]), [rich])

    def test_select_digest_respects_requested_limit(self):
        topics = {
            "vision": [paper("1")],
            "robotics": [paper("2")],
        }
        self.assertEqual(len(select_digest(topics, max_papers=1)), 1)

    def test_dislike_feedback_penalises_similar_paper(self):
        records = [{
            "paper_key": "arxiv:rated",
            "title": "Robotic apple harvesting in orchards",
            "abstract": "A robot arm detects fruit and plans harvesting motions.",
            "topic": "果园机器人",
        }]
        profile = build_dislike_profile(records)
        generic = paper("generic", abstract="A general survey of robot control.")
        orchard = paper(
            "orchard",
            abstract="An orchard robot arm detects apples for autonomous harvesting.",
        )
        selected = select_digest(
            {"果园机器人": [generic, orchard]},
            max_papers=1,
            dislike_profile=profile,
        )
        self.assertEqual(selected[0][1]["id"], "generic")

    def test_dislike_profile_matches_similar_paper(self):
        rated = {
            "paper_key": "arxiv:rated",
            "title": "Point cloud registration",
            "abstract": "A method for point cloud feature matching.",
            "topic": "点云分析",
        }
        profile = build_dislike_profile([rated])
        candidate = paper(
            "candidate",
            abstract="Point cloud registration using learned feature matching.",
        )
        self.assertGreater(dislike_similarity(candidate, profile, "点云分析"), 0)

    def test_saved_digest_contains_feedback_identity_and_text_instruction(self):
        digest = [("视觉定位", paper("1234.5678"))]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            json_path = save_json(digest, output_dir, "2026-07-30")
            markdown_path = save_markdown(digest, output_dir, "2026-07-30")

            payload = json.loads(json_path.read_text("utf-8"))
            self.assertEqual(
                payload["papers"][0]["feedback_id"],
                "arxiv:1234.5678",
            )
            markdown = markdown_path.read_text("utf-8")
            self.assertIn("disliked.txt", markdown)
            self.assertIn("2026-07-30 1", markdown)

    def test_dislike_text_file_resolves_digest_and_deduplicates(self):
        digest = [("视觉定位", paper("1234.5678"))]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            save_json(digest, output_dir, "2026-07-30")
            dislike_path = ensure_dislike_file(output_dir)
            dislike_path.write_text(
                "# comment\n2026-07-30 1\n2026-07-30 1\nbad line\n",
                encoding="utf-8",
            )

            records, warnings = load_dislikes(output_dir)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["paper_key"], "arxiv:1234.5678")
            self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
