#!/usr/bin/env python3
"""Record papers the reader dislikes and build a lightweight exclusion profile."""

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import config


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


def load_feedback(path: Path) -> list[dict]:
    """Load valid dislike records, ignoring malformed lines."""
    if not path.exists():
        return []

    records = []
    for line in path.read_text("utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        is_dislike = (
            record.get("action") == "not_interested"
            or record.get("score") == 1  # compatibility with the first prototype
        )
        if record.get("paper_key") and is_dislike:
            records.append(record)
    return records


def append_feedback(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def latest_feedback(records: list[dict]) -> list[dict]:
    """Keep one latest feedback record for each paper."""
    latest = {}
    for record in records:
        if record["paper_key"] in latest:
            del latest[record["paper_key"]]
        latest[record["paper_key"]] = record
    return list(latest.values())


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


def _normalised_paper_vector(
    paper: dict, topic: str = "",
) -> dict[str, float]:
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
    for record in latest_feedback(records):
        paper = {
            "title": record.get("title", ""),
            "abstract": record.get("abstract", ""),
            "topic": record.get("topic", ""),
        }
        vector = _normalised_paper_vector(
            paper,
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


def _resolve_paper(output_dir: Path, date_str: str, index: int) -> dict:
    digest_path = output_dir / f"{date_str}.json"
    if not digest_path.exists():
        raise ValueError(f"找不到日报：{digest_path}")
    try:
        papers = json.loads(digest_path.read_text("utf-8"))["papers"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"日报 JSON 无法读取：{digest_path}") from exc
    if index < 1 or index > len(papers):
        raise ValueError(f"论文序号应在 1 到 {len(papers)} 之间")
    return papers[index - 1]


def _dislike(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    paper = _resolve_paper(output_dir, args.date, args.paper)
    record = {
        "paper_key": paper_key(paper),
        "digest_date": args.date,
        "action": "not_interested",
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", ""),
        "topic": paper.get("topic", ""),
        "source": paper.get("source", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    append_feedback(output_dir / "feedback.jsonl", record)
    print(f"已标记为不想看：{record['title']}")


def _history(args: argparse.Namespace) -> None:
    records = latest_feedback(load_feedback(Path(args.output_dir) / "feedback.jsonl"))
    records = records[-args.limit:]
    if not records:
        print("还没有“不想看”记录。")
        return
    for record in reversed(records):
        print(f"不想看 | {record.get('title', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="标记不想看的论文，减少类似推荐")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / config.OUTPUT_DIR),
        help="日报和反馈文件目录",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    dislike = subparsers.add_parser("dislike", help="标记一篇不想看的论文")
    dislike.add_argument("paper", type=int, help="日报中的论文序号，从 1 开始")
    dislike.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    dislike.set_defaults(func=_dislike)

    history = subparsers.add_parser("history", help="查看最近的“不想看”记录")
    history.add_argument("--limit", type=int, default=20)
    history.set_defaults(func=_history)

    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
