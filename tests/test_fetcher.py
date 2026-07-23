import unittest

from fetcher import deduplicate, select_digest


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


if __name__ == "__main__":
    unittest.main()
