#!/usr/bin/env python3
"""
Daily paper fetcher — three sources:
  1. arXiv           : preprints in cs.CV / cs.RO / eess.IV
  2. Semantic Scholar: papers from target venues (CVPR/ICRA/COMPAG/…)
  3. Journal RSS     : COMPAG / Biosyst.Eng. / SmartAg / PrecAg / JFR

Output: exactly MAX_PAPERS_TOTAL papers per day, saved as YYYY-MM-DD.md

Usage:
  python3 fetcher.py               # today
  python3 fetcher.py --dry-run     # preview, no files written
  python3 fetcher.py --query "robotic apple harvesting" --label "My topic"
  python3 fetcher.py --no-s2       # skip Semantic Scholar
  python3 fetcher.py --no-rss      # skip RSS journals
  python3 fetcher.py --no-cache    # ignore seen-paper cache
"""

import arxiv
import requests
import feedparser
import json
import re
import argparse
import smtplib
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

ARXIV_DELAY = 4.0   # seconds between arXiv API calls
S2_DELAY    = 3.0   # seconds between Semantic Scholar calls


# ══════════════════════════════════════════════════════════════════════════════
#  Source 1 — arXiv
# ══════════════════════════════════════════════════════════════════════════════

def _arxiv_primary(result) -> str:
    pc = result.primary_category
    return pc if isinstance(pc, str) else (pc.id if pc else "")


def _arxiv_query(query: str, categories: list[str],
                 max_results: int, since: datetime) -> list[dict]:
    cat_filter = " OR ".join(f"cat:{c}" for c in categories)
    client = arxiv.Client(page_size=50, delay_seconds=3.0, num_retries=5)
    search = arxiv.Search(
        query=f"({query}) AND ({cat_filter})",
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    papers = []
    for r in client.results(search):
        pub_dt = r.published.replace(tzinfo=timezone.utc)
        if pub_dt < since:
            break
        raw_id   = r.entry_id.split("/abs/")[-1]
        arxiv_id = raw_id.split("v")[0]
        papers.append({
            "id": arxiv_id, "arxiv_id": arxiv_id, "doi": "",
            "source": "arXiv", "title": r.title.strip(),
            "authors": [a.name for a in r.authors[:6]],
            "abstract": r.summary.strip().replace("\n", " "),
            "published": pub_dt.strftime("%Y-%m-%d"),
            "url": r.entry_id, "pdf_url": r.pdf_url,
            "venue": _arxiv_primary(r),
        })
    return papers


def fetch_arxiv_topic(topic: dict, since: datetime) -> list[dict]:
    collected = []
    for query in topic["arxiv_queries"]:
        print(f"    [arXiv] {query!r} ...", end=" ", flush=True)
        try:
            papers = _arxiv_query(query, config.ARXIV_CATEGORIES,
                                  config.MAX_RESULTS_PER_QUERY, since)
            print(f"{len(papers)}")
            collected.extend(papers)
        except Exception as e:
            print(f"ERR: {e}")
        time.sleep(ARXIV_DELAY)
    return collected


# ══════════════════════════════════════════════════════════════════════════════
#  Source 2 — Semantic Scholar
# ══════════════════════════════════════════════════════════════════════════════

def _venue_matches(venue: str) -> bool:
    v = venue.lower()
    return any(t.lower() in v for t in config.S2_TARGET_VENUES)


def _shorten_venue(venue: str) -> str:
    v = venue.lower()
    for key, short in {
        "cvpr": "CVPR", "iccv": "ICCV", "eccv": "ECCV", "wacv": "WACV",
        "neurips": "NeurIPS", "iclr": "ICLR", "icml": "ICML",
        "aaai": "AAAI", "ijcai": "IJCAI",
        "icra": "ICRA", "iros": "IROS",
        "robotics and automation letters": "RA-L",
        "international journal of robotics": "IJRR",
        "computers and electronics in agriculture": "COMPAG",
        "artificial intelligence in agriculture": "AIA",
        "biosystems engineering": "Biosyst.Eng.",
        "precision agriculture": "PrecAg",
        "smart agricultural": "SmartAg",
        "ieee transactions on pattern analysis": "TPAMI",
        "international journal of computer vision": "IJCV",
        "ieee transactions on image processing": "TIP",
        "ieee transactions on geoscience": "TGRS",
        "journal of field robotics": "JFR",
    }.items():
        if key in v:
            return short
    return venue[:20]


def _s2_query(query: str, since: datetime,
              max_results: int = 50, api_key: str = "") -> list[dict]:
    url     = "https://api.semanticscholar.org/graph/v1/paper/search"
    headers = {"x-api-key": api_key} if api_key else {}
    params  = {
        "query":                 query,
        "fields":                "paperId,title,authors,abstract,year,publicationDate,venue,externalIds,openAccessPdf",
        "limit":                 min(max_results, 100),
        "publicationDateOrYear": since.strftime("%Y-%m-%d") + ":",
    }
    for attempt in range(4):
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 429:
            wait = 15 * (2 ** attempt)
            print(f"rate-limit, waiting {wait}s …", end=" ", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code == 403:
            raise RuntimeError("S2 API key 无效或无权限（403），请检查 config.py 中的 S2_API_KEY")
        resp.raise_for_status()
        break
    else:
        raise RuntimeError("S2 rate-limited after retries")

    papers = []
    for p in resp.json().get("data", []):
        venue = (p.get("venue") or "").strip()
        if not _venue_matches(venue):
            continue
        pub_str = p.get("publicationDate") or str(p.get("year", ""))
        if not pub_str:
            continue
        try:
            pub_dt = (datetime(int(pub_str), 1, 1, tzinfo=timezone.utc)
                      if len(pub_str) == 4
                      else datetime.strptime(pub_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc))
        except Exception:
            continue
        if pub_dt < since:
            continue
        ext      = p.get("externalIds") or {}
        doi      = ext.get("DOI", "")
        arxiv_id = ext.get("ArXiv", "").split("v")[0]
        pdf      = (p.get("openAccessPdf") or {}).get("url")
        link     = (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else
                    f"https://doi.org/{doi}"            if doi       else
                    f"https://www.semanticscholar.org/paper/{p.get('paperId','')}")
        short = _shorten_venue(venue)
        papers.append({
            "id": arxiv_id or doi or p.get("paperId", ""),
            "arxiv_id": arxiv_id, "doi": doi,
            "source": short,
            "title": (p.get("title") or "").strip(),
            "authors": [a["name"] for a in (p.get("authors") or [])[:6]],
            "abstract": (p.get("abstract") or "").strip().replace("\n", " "),
            "published": pub_str[:10] if len(pub_str) >= 10 else pub_str,
            "url": link, "pdf_url": pdf, "venue": venue,
        })
    return papers


def fetch_s2_topic(topic: dict, since: datetime) -> list[dict]:
    s2_since = min(since,
                   datetime.now(timezone.utc) - timedelta(days=config.S2_LOOKBACK_DAYS))
    collected = []
    for query in topic.get("s2_queries", []):
        print(f"    [S2]    {query!r} ...", end=" ", flush=True)
        try:
            papers = _s2_query(query, s2_since,
                               config.MAX_S2_RESULTS_PER_QUERY, config.S2_API_KEY)
            print(f"{len(papers)} (venue-filtered)")
            collected.extend(papers)
        except Exception as e:
            print(f"ERR: {e}")
        time.sleep(S2_DELAY)
    return collected


# ══════════════════════════════════════════════════════════════════════════════
#  Source 3 — Journal RSS
# ══════════════════════════════════════════════════════════════════════════════

_MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,
    "sep":9,"oct":10,"nov":11,"dec":12,
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _rss_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    # ScienceDirect: date in summary HTML
    summary = entry.get("summary", "") or ""
    for pat in (
        r"(?:Publication date|Available online)[:\s]+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        r"(?:Publication date|Available online)[:\s]+([A-Za-z]+)\s+(\d{4})",
    ):
        m = re.search(pat, summary, re.IGNORECASE)
        if m:
            try:
                g = m.groups()
                if len(g) == 3:
                    d, mo, y = int(g[0]), _MONTH_MAP[g[1].lower()], int(g[2])
                else:
                    d, mo, y = 1, _MONTH_MAP[g[0].lower()], int(g[1])
                return datetime(y, mo, d, tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_rss_journal(journal: dict, since: datetime) -> list[dict]:
    try:
        feed = feedparser.parse(journal["url"])
    except Exception as e:
        print(f"RSS ERR: {e}")
        return []

    date_in_html = journal.get("date_in_html", False)
    papers = []
    for entry in feed.entries:
        pub_dt       = _rss_date(entry)
        pub_date_str = pub_dt.strftime("%Y-%m-%d") if pub_dt else datetime.now().strftime("%Y-%m-%d")

        if not date_in_html and (pub_dt is None or pub_dt < since):
            continue

        title   = _strip_html(entry.get("title", ""))
        link    = entry.get("link", "")
        summary = entry.get("summary", "") or ""
        doi     = (_strip_html(getattr(entry, "prism_doi", "") or
                               entry.get("prism_doi", "") or
                               entry.get("dc_identifier", "")))

        raw_author = entry.get("author", "")
        if raw_author:
            authors = [a.strip() for a in raw_author.split(",")]
        else:
            m = re.search(r"Author\(s\):\s*([^<]+)", summary, re.IGNORECASE)
            authors = [a.strip() for a in m.group(1).split(",")] if m else []

        abstract = "" if date_in_html else _strip_html(summary)

        papers.append({
            "id": doi or link, "arxiv_id": "", "doi": doi,
            "source": journal["abbr"],
            "title": title, "authors": authors, "abstract": abstract,
            "published": pub_date_str,
            "url": link, "pdf_url": None, "venue": journal["name"],
        })
    return papers


def _matches_topic(paper: dict, topic: dict) -> bool:
    text = (paper["title"] + " " + paper["abstract"]).lower()
    return any(kw.lower() in text for kw in topic.get("rss_keywords", []))


# ══════════════════════════════════════════════════════════════════════════════
#  Deduplication
# ══════════════════════════════════════════════════════════════════════════════

def _key(paper: dict) -> str:
    if paper.get("arxiv_id"):
        return f"arxiv:{paper['arxiv_id']}"
    if paper.get("doi"):
        return f"doi:{paper['doi'].lower()}"
    return f"id:{paper['id']}"


def deduplicate(papers: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for p in papers:
        k = _key(p)
        if k not in seen or len(p["abstract"]) > len(seen[k]["abstract"]):
            seen[k] = p
    result = list(seen.values())
    result.sort(key=lambda p: p["published"], reverse=True)
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Seen-paper cache
# ══════════════════════════════════════════════════════════════════════════════

CACHE_FILE          = Path(__file__).parent / config.OUTPUT_DIR / ".seen_papers.json"
CACHE_RETENTION     = 45  # days


def load_cache() -> dict[str, str]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache: dict[str, str]):
    cutoff = (datetime.now() - timedelta(days=CACHE_RETENTION)).strftime("%Y-%m-%d")
    pruned = {k: v for k, v in cache.items() if v >= cutoff}
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(pruned, indent=2), encoding="utf-8")


def filter_new(papers: list[dict], cache: dict[str, str], today: str) -> list[dict]:
    new = []
    for p in papers:
        k = _key(p)
        if k not in cache:
            cache[k] = today
            new.append(p)
    return new


# ══════════════════════════════════════════════════════════════════════════════
#  Orchestration
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all(lookback_days: int, use_s2: bool,
              use_rss: bool, topics: list[dict] | None = None) -> dict[str, list[dict]]:
    """
    Fetch raw papers (no cache logic here).
    Returns {topic_label: [deduplicated papers]}.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    topics = topics or config.TOPICS
    topic_results: dict[str, list] = {}

    for topic in topics:
        label = topic["label"]
        print(f"\n── {label} ──")
        papers = fetch_arxiv_topic(topic, since)
        if use_s2:
            papers += fetch_s2_topic(topic, since)
        papers = deduplicate(papers)
        n_ax = sum(1 for p in papers if p["source"] == "arXiv")
        print(f"  => {len(papers)} 篇  ({n_ax} arXiv | {len(papers)-n_ax} venue)")
        topic_results[label] = papers

    if use_rss:
        print("\n── 期刊 RSS ──")
        for journal in config.RSS_JOURNALS:
            print(f"  [{journal['abbr']}] …", end=" ", flush=True)
            rss_papers = fetch_rss_journal(journal, since)
            print(f"{len(rss_papers)} 篇")
            for paper in rss_papers:
                for topic in topics:
                    if _matches_topic(paper, topic):
                        existing = {_key(p) for p in topic_results[topic["label"]]}
                        if _key(paper) not in existing:
                            topic_results[topic["label"]].append(paper)

    return topic_results


def _apply_window(all_results: dict, days: int) -> dict:
    """Keep only papers published within the last `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return {
        label: [p for p in papers if p["published"] >= cutoff]
        for label, papers in all_results.items()
    }


def progressive_select(all_results: dict, cache: dict,
                       max_papers: int | None = None) -> tuple[list, int]:
    """
    Filter out seen papers, then try progressively wider windows
    until MAX_PAPERS_TOTAL papers are found.
    Returns (digest, days_used).
    """
    # Remove already-seen papers
    new_results = {
        label: [p for p in papers if _key(p) not in cache]
        for label, papers in all_results.items()
    }

    max_papers = max_papers or config.MAX_PAPERS_TOTAL
    total_new = sum(len(v) for v in new_results.values())
    print(f"\n共 {total_new} 篇未读论文，逐步缩小时间窗口…")

    for days in config.LOOKBACK_STEPS:
        window = _apply_window(new_results, days)
        digest = select_digest(window, max_papers)
        n = len(digest)
        if n >= max_papers:
            print(f"  过去 {days} 天 → {n} 篇，足够 ✓")
            return digest, days
        print(f"  过去 {days} 天 → {n} 篇，不足 {max_papers}，扩大…")

    # Fallback: use all new papers regardless of date
    digest = select_digest(new_results, max_papers)
    return digest, max(config.LOOKBACK_STEPS)


# ══════════════════════════════════════════════════════════════════════════════
#  Digest selection  (global top-N, one per topic at most)
# ══════════════════════════════════════════════════════════════════════════════

def _score(paper: dict) -> int:
    src = paper.get("source", "")
    if src == "arXiv":
        return 1
    if src in ("COMPAG", "AIA", "Biosyst.Eng.", "SmartAg", "PrecAg", "JFR"):
        return 3
    return 2  # CV/robotics conference


def select_digest(topic_results: dict,
                  max_papers: int | None = None) -> list[tuple[str, dict]]:
    """One best paper per topic → sort by quality → return top MAX_PAPERS_TOTAL."""
    max_papers = max_papers or config.MAX_PAPERS_TOTAL
    candidates: list[tuple[str, dict]] = []
    for label, papers in topic_results.items():
        if not papers:
            continue
        best = max(papers, key=lambda p: (_score(p), p["published"]))
        candidates.append((label, best))
    candidates.sort(key=lambda t: (_score(t[1]), t[1]["published"]), reverse=True)
    return candidates[:max_papers]


# ══════════════════════════════════════════════════════════════════════════════
#  AI Summarisation  —  backends: gemini | ollama | none
# ══════════════════════════════════════════════════════════════════════════════

def _build_prompt(title: str, abstract: str) -> str:
    if abstract:
        return (
            "请用3句中文概括以下论文的核心问题、主要方法和关键结论，"
            "语言简洁，不需要任何前言或引导句，直接给出3句话。\n\n"
            f"标题：{title}\n\n摘要：{abstract}"
        )
    return (
        "根据以下论文标题，用3句中文推测该研究的核心问题、可能的方法和预期贡献，"
        "语言简洁，直接给出3句话。\n\n"
        f"标题：{title}"
    )


def _summarise_gemini(title: str, abstract: str) -> str:
    import os
    from google import genai
    key = config.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 未设置，请填入 config.py 或环境变量")
    client = genai.Client(api_key=key)
    resp   = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=_build_prompt(title, abstract),
    )
    return resp.text.strip()


def _summarise_ollama(title: str, abstract: str) -> str:
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/generate",
        json={"model": config.OLLAMA_MODEL,
              "prompt": _build_prompt(title, abstract),
              "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def summarise_paper(title: str, abstract: str) -> str:
    backend = config.SUMMARY_BACKEND
    try:
        if backend == "gemini":
            return _summarise_gemini(title, abstract)
        if backend == "ollama":
            return _summarise_ollama(title, abstract)
        return ""   # "none" — skip
    except Exception as e:
        return f"（总结失败：{e}）"


def enrich_digest(digest: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Add 'summary_zh' field to each paper in the digest."""
    enriched = []
    for i, (label, paper) in enumerate(digest, 1):
        print(f"  [{i}/{len(digest)}] 总结: {paper['title'][:55]}…")
        summary = summarise_paper(paper["title"], paper.get("abstract", ""))
        enriched.append((label, {**paper, "summary_zh": summary}))
    return enriched


# ══════════════════════════════════════════════════════════════════════════════
#  Output
# ══════════════════════════════════════════════════════════════════════════════

def save_markdown(digest: list[tuple[str, dict]], output_dir: Path, date_str: str) -> Path:
    path  = output_dir / f"{date_str}.md"
    lines = [f"# 论文日报 — {date_str}", ""]

    for i, (label, p) in enumerate(digest, 1):
        src         = p.get("source", "")
        venue_disp  = p.get("venue") or src
        authors_str = ", ".join(p["authors"])
        if len(p["authors"]) == 6:
            authors_str += " et al."

        lines.append(f"## {i}. {p['title']}")
        lines.append(f"**链接:** {p['url']}")
        lines.append(f"**主题:** {label}　**来源:** `{src}`　**发表:** {p['published']}")
        if authors_str:
            lines.append(f"**作者:** {authors_str}")
        if venue_disp and venue_disp != src:
            lines.append(f"**期刊/会议:** {venue_disp}")
        if p.get("doi"):
            lines.append(f"**DOI:** https://doi.org/{p['doi']}")
        if p.get("pdf_url"):
            lines.append(f"**PDF:** {p['pdf_url']}")

        # AI Chinese summary
        if p.get("summary_zh"):
            lines.append(f"\n**内容总结:**\n{p['summary_zh']}")

        # Original abstract (collapsed, for reference)
        if p.get("abstract"):
            snip = p["abstract"][:400] + ("…" if len(p["abstract"]) > 400 else "")
            lines.append(f"\n<details><summary>原文摘要</summary>\n\n{snip}\n\n</details>")

        lines.append("\n---\n")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def save_json(digest: list[tuple[str, dict]], output_dir: Path, date_str: str) -> Path:
    path = output_dir / f"{date_str}.json"
    path.write_text(
        json.dumps({
            "date":       date_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "papers":     [{"topic": label, **paper} for label, paper in digest],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def print_digest(digest: list[tuple[str, dict]]):
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    t = Table(title=f"今日日报 ({len(digest)} 篇)", box=box.ROUNDED, show_lines=True)
    t.add_column("#",      width=3,  justify="right")
    t.add_column("主题",   style="cyan",   no_wrap=True)
    t.add_column("来源",   style="yellow", width=14)
    t.add_column("标题",   style="white")

    for i, (label, p) in enumerate(digest, 1):
        title = p["title"][:65] + ("…" if len(p["title"]) > 65 else "")
        t.add_row(str(i), label, p["source"], title)

    console.print(t)


def send_email(md_content: str, date_str: str):
    if not config.EMAIL_TO:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[论文日报] {date_str}"
        msg["From"]    = config.EMAIL_FROM
        msg["To"]      = config.EMAIL_TO
        msg.attach(MIMEText(md_content, "plain", "utf-8"))
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(config.EMAIL_FROM, config.EMAIL_PASSWORD)
            smtp.sendmail(config.EMAIL_FROM, config.EMAIL_TO, msg.as_string())
        print(f"邮件 → {config.EMAIL_TO}")
    except Exception as e:
        print(f"邮件失败: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="多来源论文检索与每日摘要")
    parser.add_argument("--dry-run",    action="store_true", help="预览，不保存文件和缓存")
    parser.add_argument("--no-s2",      action="store_true", help="跳过 Semantic Scholar")
    parser.add_argument("--no-rss",     action="store_true", help="跳过 RSS 期刊")
    parser.add_argument("--no-cache",   action="store_true", help="忽略已读缓存，重新选")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--query", action="append", metavar="KEYWORDS",
        help="自定义检索词，可重复使用；提供后将替代 config.py 中的 TOPICS",
    )
    parser.add_argument(
        "--label", default="自定义检索", help="自定义检索主题名称（配合 --query）",
    )
    parser.add_argument(
        "--max-papers", type=int, default=config.MAX_PAPERS_TOTAL,
        help=f"本次最多输出几篇（默认 {config.MAX_PAPERS_TOTAL}）",
    )
    args = parser.parse_args()

    if args.max_papers < 1:
        parser.error("--max-papers 必须大于 0")

    topics = config.TOPICS
    if args.query:
        topics = [{
            "label": args.label,
            "arxiv_queries": args.query,
            "s2_queries": args.query,
            "rss_keywords": args.query,
        }]

    max_days = max(config.LOOKBACK_STEPS)
    print(f"抓取过去 {max_days} 天论文，最多输出 {args.max_papers} 篇\n")

    # ── 1. 一次性抓取最大窗口 ───────────────────────────────────────────────
    all_results = fetch_all(
        lookback_days=max_days,
        use_s2=not args.no_s2,
        use_rss=not args.no_rss,
        topics=topics,
    )

    # ── 2. 读缓存 + 逐步缩小时间窗口选出今日 3 篇 ──────────────────────────
    cache = load_cache() if not args.no_cache else {}
    digest, used_days = progressive_select(all_results, cache, args.max_papers)

    print()
    print_digest(digest)

    if args.dry_run:
        print("\n[dry-run] 跳过保存")
        return

    # ── 3. 仅将今日选出的 3 篇写入缓存（其余留给后续天使用）────────────────
    if not args.no_cache:
        today = datetime.now().strftime("%Y-%m-%d")
        for _, paper in digest:
            cache[_key(paper)] = today
        save_cache(cache)

    print("\n正在生成中文总结…")
    digest = enrich_digest(digest)

    out_dir = (Path(args.output_dir) if args.output_dir
               else Path(__file__).parent / config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    md_path   = save_markdown(digest, out_dir, date_str)
    json_path = save_json(digest, out_dir, date_str)

    print(f"\n已保存: {md_path}")

    if config.EMAIL_TO:
        send_email(md_path.read_text("utf-8"), date_str)


if __name__ == "__main__":
    main()
