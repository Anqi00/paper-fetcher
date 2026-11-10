import json
import tempfile
import unittest
from pathlib import Path

from feedback import (
    build_dislike_profile,
    disliked_entries,
    set_dislike,
    dislike_similarity,
    ensure_dislike_file,
    load_dislikes,
)
from fetcher import (
    build_arxiv_query,
    deduplicate,
    relevance,
    save_json,
    save_markdown,
    score_and_filter,
    select_digest,
)
from summarizer import (
    _abstract_from_inverted_index,
    clean_elsevier_text,
    drop_unsupported_urls,
    parse_summary,
    render_summary,
    split_chunks,
)


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


    def test_set_dislike_toggles_line_and_keeps_comments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            set_dislike(output_dir, "2026-11-10", 2, True)
            set_dislike(output_dir, "2026-11-10", 2, True)
            self.assertEqual(disliked_entries(output_dir), {("2026-11-10", 2)})
            set_dislike(output_dir, "2026-11-10", 2, False)
            self.assertEqual(disliked_entries(output_dir), set())
            self.assertIn("# 不想看的论文", (output_dir / "disliked.txt").read_text("utf-8"))

    def test_relevance_prefers_title_hits_and_filters_off_topic(self):
        topic = {"label": "3DGS", "keywords": ["gaussian splat", "3dgs"]}
        on_topic = {**paper("a"), "title": "Fast 3D Gaussian Splatting"}
        abstract_only = paper("b", abstract="We compare with 3DGS baselines.")
        off_topic = {**paper("c"), "title": "Tomato harvesting robot"}
        self.assertEqual(relevance(on_topic, topic), 2)
        self.assertEqual(relevance(abstract_only, topic), 1)
        kept = score_and_filter([on_topic, abstract_only, off_topic],
                                {**topic, "min_relevance": 2})
        self.assertEqual([p["id"] for p in kept], ["a"])

    def test_relevant_arxiv_beats_irrelevant_journal(self):
        journal = {**paper("j", source="COMPAG"), "relevance": 2}
        preprint = {**paper("x"), "relevance": 6}
        selected = select_digest({"t1": [journal], "t2": [preprint]}, max_papers=1)
        self.assertEqual(selected[0][1]["id"], "x")

    def test_arxiv_query_requires_all_words(self):
        self.assertEqual(
            build_arxiv_query("3D Gaussian splatting", ["cs.CV"]),
            "(all:3D AND all:Gaussian AND all:splatting) AND (cat:cs.CV)",
        )
        self.assertEqual(
            build_arxiv_query('ti:"feature matching"', ["cs.CV"]),
            '(ti:"feature matching") AND (cat:cs.CV)',
        )

    def test_parse_summary_normalises_model_output(self):
        raw = """```json
        {"tldr": "一句话", "innovations": "点一；点二", "rating": "9"}
        ```"""
        summary = parse_summary(raw)
        self.assertEqual(summary["innovations"], ["点一", "点二"])
        self.assertEqual(summary["rating"], 5)
        self.assertEqual(parse_summary("not json")["error"], "模型未返回有效 JSON")
        nested = parse_summary('{"experiments": {"数据集": ["A", "B"], "指标": "PSNR"}}')
        self.assertEqual(nested["experiments"], "数据集：A、B；指标：PSNR")

    def test_render_summary_hides_missing_fields(self):
        item = {**paper("1"), "summary": {
            "tldr": "用 X 解决 Y", "results": "未提及", "innovations": ["A"],
            "rating": 4, "rating_reason": "相关",
        }}
        text = "\n".join(render_summary(item))
        self.assertIn("★★★★☆", text)
        self.assertIn("1. A", text)
        self.assertNotIn("主要结果", text)

    def test_split_chunks_covers_text_within_limits(self):
        paras = [f"Paragraph {i} " + "x" * 900 for i in range(30)]
        text = "\n\n".join(paras)
        chunks = split_chunks(text, 4000, max_chunks=10)
        self.assertTrue(all(len(c) <= 4000 for c in chunks))
        self.assertIn("Paragraph 29", chunks[-1])
        self.assertEqual(len(split_chunks(text, 4000, max_chunks=2)), 2)
        long_para = split_chunks("y" * 10000, 4000, max_chunks=10)
        self.assertEqual([len(c) for c in long_para], [4000, 4000, 2000])
        sentences = split_chunks("A sentence here. " * 600, 4000, max_chunks=10)
        self.assertTrue(all(c.endswith(".") for c in sentences[:-1]))

    def test_clean_elsevier_text_keeps_body_only(self):
        raw = (
            "serial JL 2026 image/jpeg fx1.jpg 1 Introduction 2 Methods TOC "
            + "1 Introduction Branches matter. " + "Body text. " * 800
            + "5 Conclusions Done. CRediT authorship contribution statement X "
            + "References Smith 2020"
        )
        body = clean_elsevier_text(raw)
        self.assertTrue(body.startswith("1 Introduction Branches matter."))
        self.assertIn("5 Conclusions Done.", body)
        self.assertNotIn("CRediT", body)
        self.assertNotIn("Smith 2020", body)

    def test_invented_links_are_removed(self):
        summary = {"resources": "代码公开：https://github.com/your-repo-url",
                   "method": "见 https://github.com/real/repo 。"}
        cleaned = drop_unsupported_urls(summary, "code at https://github.com/real/repo.")
        self.assertEqual(cleaned["resources"], "代码公开")
        self.assertIn("https://github.com/real/repo", cleaned["method"])

    def test_parse_summary_strips_echoed_prompt_labels(self):
        summary = parse_summary('{"ablation": "哪个模块贡献最大：Relation3D"}')
        self.assertEqual(summary["ablation"], "Relation3D")

    def test_openalex_abstract_reconstruction(self):
        index = {"Gaussian": [1], "Splatting": [2], "3D": [0]}
        self.assertEqual(_abstract_from_inverted_index(index), "3D Gaussian Splatting")

if __name__ == "__main__":
    unittest.main()
