#!/usr/bin/env python3
"""Record paper ratings and build a lightweight preference profile."""

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
    """Load valid JSONL records, ignoring malformed lines."""
    if not path.exists():
        return []

    records = []
    for line in path.read_text("utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("paper_key") and record.get("score") in range(1, 6):
            records.append(record)
    return records


def append_feedback(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def latest_feedback(records: list[dict]) -> list[dict]:
    """Keep the most recently appended rating for each paper."""
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
    paper: dict, topic: str = "", tags: list[str] | None = None,
) -> dict[str, float]:
    vector: Counter[str] = Counter()
    for feature in _features(paper.get("title", "")):
        vector[feature] += 2.0
    for feature in _features(paper.get("abstract", "")):
        vector[feature] += 1.0
    for feature in _features(topic or paper.get("topic", "")):
        vector[feature] += 2.0
    for tag in tags or []:
        for feature in _features(tag):
            vector[feature] += 3.0

    norm = math.sqrt(sum(value * value for value in vector.values()))
    if not norm:
        return {}
    return {feature: value / norm for feature, value in vector.items()}


def build_preference_profile(records: list[dict]) -> dict[str, float]:
    """
    Build a unit preference vector from the latest ratings.

    Scores above 3 contribute positive features; scores below 3 contribute
    negative features. A score of 3 is neutral.
    """
    profile: defaultdict[str, float] = defaultdict(float)
    for record in latest_feedback(records):
        signal = (record["score"] - 3) / 2
        if not signal:
            continue
        paper = {
            "title": record.get("title", ""),
            "abstract": record.get("abstract", ""),
            "topic": record.get("topic", ""),
        }
        vector = _normalised_paper_vector(
            paper,
            topic=record.get("topic", ""),
            tags=record.get("tags", []),
        )
        for feature, value in vector.items():
            profile[feature] += signal * value

    norm = math.sqrt(sum(value * value for value in profile.values()))
    if not norm:
        return {}
    return {feature: value / norm for feature, value in profile.items()}


def preference_score(
    paper: dict, profile: dict[str, float], topic: str = "",
) -> float:
    """Cosine-like relevance in the range [-1, 1]."""
    if not profile:
        return 0.0
    vector = _normalised_paper_vector(paper, topic=topic)
    return sum(profile.get(feature, 0.0) * value for feature, value in vector.items())


def _parse_tags(value: str) -> list[str]:
    return [tag.strip() for tag in re.split(r"[,，]", value) if tag.strip()]


def _resolve_paper(output_dir: Path, date_str: str, index: int) -> dict:
    digest_path = output_dir / f"{date_str}.json"
    if not digest_path.exists():
        raise ValueError(f"找不到日报：{digest_path}")
    try:
        papers = json.loads(digest_path.read_text("utf-8"))["papers"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"日报 JSON 无法读取：{digest_path}") from exc
    if index < 1 or index > len(papers):
        raise ValueError(f"--paper 应在 1 到 {len(papers)} 之间")
    return papers[index - 1]


def _rate(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    paper = _resolve_paper(output_dir, args.date, args.paper)
    record = {
        "paper_key": paper_key(paper),
        "digest_date": args.date,
        "score": args.score,
        "tags": _parse_tags(args.tags),
        "note": args.note.strip(),
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", ""),
        "topic": paper.get("topic", ""),
        "source": paper.get("source", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    append_feedback(output_dir / "feedback.jsonl", record)
    print(f"已记录 {args.score} 分：{record['title']}")


def _history(args: argparse.Namespace) -> None:
    records = latest_feedback(load_feedback(Path(args.output_dir) / "feedback.jsonl"))
    records = records[-args.limit:]
    if not records:
        print("还没有评分记录。")
        return
    for record in reversed(records):
        tags = f" [{', '.join(record.get('tags', []))}]" if record.get("tags") else ""
        print(f"{record['score']} 分 | {record.get('title', '')}{tags}")


def main() -> None:
    parser = argparse.ArgumentParser(description="为论文日报评分并更新本地偏好")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / config.OUTPUT_DIR),
        help="日报和反馈文件目录",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    rate = subparsers.add_parser("rate", help="按日报中的论文序号评分")
    rate.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    rate.add_argument("--paper", type=int, required=True, help="日报中的论文序号，从 1 开始")
    rate.add_argument("--score", type=int, choices=range(1, 6), required=True)
    rate.add_argument("--tags", default="", help="可选标签，使用逗号分隔")
    rate.add_argument("--note", default="", help="可选备注")
    rate.set_defaults(func=_rate)

    history = subparsers.add_parser("history", help="查看最近的评分")
    history.add_argument("--limit", type=int, default=20)
    history.set_defaults(func=_history)

    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
