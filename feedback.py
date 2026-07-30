"""Load disliked papers from a text file and build an exclusion profile."""

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


DISLIKE_FILENAME = "disliked.txt"

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can",
    "for", "from", "has", "have", "in", "is", "it", "its", "of", "on",
    "or", "our", "paper", "propose", "proposed", "study", "that", "the",
    "their", "these", "this", "to", "using", "via", "we", "with",
}


def paper_key(paper: dict) -> str:
    """Return the same stable identity used by the fetcher cache."""
    if paper.get("feedback_id"):
        return str(paper["feedback_id"])
    if paper.get("arxiv_id"):
        return f"arxiv:{paper['arxiv_id']}"
    if paper.get("doi"):
        return f"doi:{str(paper['doi']).lower()}"
    return f"id:{paper.get('id', '')}"


def ensure_dislike_file(output_dir: Path) -> Path:
    """Create the editable dislike file with a short example when absent."""
    path = output_dir / DISLIKE_FILENAME
    if not path.exists():
        path.write_text(
            "# 不想看的论文：每行填写“日报日期 论文序号”\n"
            "# 示例：2026-07-24 2\n",
            encoding="utf-8",
        )
    return path


def load_dislikes(output_dir: Path) -> tuple[list[dict], list[str]]:
    """
    Resolve ``YYYY-MM-DD N`` lines to papers stored in daily JSON digests.

    Returns deduplicated paper records and human-readable warnings for lines
    that cannot be resolved.
    """
    path = output_dir / DISLIKE_FILENAME
    if not path.exists():
        return [], []

    records = {}
    warnings = []
    digest_cache: dict[str, list[dict] | None] = {}

    for line_number, raw_line in enumerate(path.read_text("utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s+(\d+)", line)
        if not match:
            warnings.append(
                f"{DISLIKE_FILENAME} 第 {line_number} 行格式无效：{raw_line}"
            )
            continue

        date_str, index_text = match.groups()
        if date_str not in digest_cache:
            digest_path = output_dir / f"{date_str}.json"
            try:
                payload = json.loads(digest_path.read_text("utf-8"))
                digest_cache[date_str] = payload["papers"]
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                digest_cache[date_str] = None

        papers = digest_cache[date_str]
        if papers is None:
            warnings.append(
                f"{DISLIKE_FILENAME} 第 {line_number} 行找不到日报：{date_str}.json"
            )
            continue

        index = int(index_text)
        if index < 1 or index > len(papers):
            warnings.append(
                f"{DISLIKE_FILENAME} 第 {line_number} 行论文序号应在 "
                f"1 到 {len(papers)} 之间"
            )
            continue

        paper = papers[index - 1]
        key = paper_key(paper)
        records[key] = {
            "paper_key": key,
            "digest_date": date_str,
            "title": paper.get("title", ""),
            "abstract": paper.get("abstract", ""),
            "topic": paper.get("topic", ""),
            "source": paper.get("source", ""),
        }

    return list(records.values()), warnings


def _features(text: str) -> set[str]:
    tokens = re.findall(
        r"[a-z0-9][a-z0-9.+-]*|[\u4e00-\u9fff]{2,}",
        (text or "").lower(),
    )
    tokens = [
        token.strip(".+-")
        for token in tokens
        if token not in _STOPWORDS and not token.isdigit()
    ]
    tokens = [token for token in tokens if len(token) > 1]
    features = set(tokens)
    english = [token for token in tokens if token.isascii()]
    features.update(f"{left} {right}" for left, right in zip(english, english[1:]))
    return features


def _normalised_paper_vector(paper: dict, topic: str = "") -> dict[str, float]:
    vector: Counter[str] = Counter()
    for feature in _features(paper.get("title", "")):
        vector[feature] += 2.0
    for feature in _features(paper.get("abstract", "")):
        vector[feature] += 1.0
    for feature in _features(topic or paper.get("topic", "")):
        vector[feature] += 2.0

    norm = math.sqrt(sum(value * value for value in vector.values()))
    if not norm:
        return {}
    return {feature: value / norm for feature, value in vector.items()}


def build_dislike_profile(records: list[dict]) -> dict[str, float]:
    """Build a unit vector describing papers the reader does not want."""
    profile: defaultdict[str, float] = defaultdict(float)
    for record in records:
        vector = _normalised_paper_vector(
            record,
            topic=record.get("topic", ""),
        )
        for feature, value in vector.items():
            profile[feature] += value

    norm = math.sqrt(sum(value * value for value in profile.values()))
    if not norm:
        return {}
    return {feature: value / norm for feature, value in profile.items()}


def dislike_similarity(
    paper: dict, profile: dict[str, float], topic: str = "",
) -> float:
    """Return similarity to previously disliked papers in the range [0, 1]."""
    if not profile:
        return 0.0
    vector = _normalised_paper_vector(paper, topic=topic)
    return sum(profile.get(feature, 0.0) * value for feature, value in vector.items())
