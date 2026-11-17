"""
Structured Chinese summaries for the papers in a daily digest.

Pipeline per paper:
  1. enrich metadata: DOI (Crossref), abstract / arXiv version / open-access
     PDF (Semantic Scholar, OpenAlex, Elsevier API)
  2. fetch full text: arXiv HTML → arXiv PDF → Elsevier API → open-access PDF
  3. long papers are read in chunks: each chunk → notes, notes → final summary
  4. the LLM (ollama | gemini) returns a JSON summary with fixed fields
  5. render the summary as compact Markdown
"""

import io
import json
import re
from html.parser import HTMLParser

import requests

import config

USER_AGENT = "paper-fetcher/1.0 (daily research digest)"

# (field, Markdown label) in reading order; tldr / relevance / rating are
# rendered separately at the top of each paper because they drive triage.
DETAIL_FIELDS = [
    ("problem", "解决的问题"),
    ("innovations", "创新点"),
    ("method", "方法"),
    ("experiments", "实验设置"),
    ("results", "主要结果"),
    ("ablation", "消融/分析结论"),
    ("limitations", "局限/存疑"),
    ("resources", "代码/数据"),
]

BASIS_LABELS = {"fulltext": "全文", "abstract": "摘要", "title": "仅标题"}


# ══════════════════════════════════════════════════════════════════════════════
#  Input text: abstract enrichment and full text
# ══════════════════════════════════════════════════════════════════════════════

def _get_json(url: str, **kwargs) -> dict:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    resp = requests.get(url, headers=headers, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def _doi(paper: dict) -> str:
    return paper.get("doi") or paper.get("resolved_doi") or ""


def _from_semantic_scholar(paper: dict) -> dict:
    headers = {"x-api-key": config.S2_API_KEY} if config.S2_API_KEY else {}
    data = _get_json(
        "https://api.semanticscholar.org/graph/v1/paper/search/match",
        params={"query": paper["title"],
                "fields": "title,abstract,externalIds,openAccessPdf"},
        headers=headers,
    )
    hit = (data.get("data") or [{}])[0]
    if _normalise_title(hit.get("title", "")) != _normalise_title(paper["title"]):
        return {}
    ext = hit.get("externalIds") or {}
    return {
        "abstract": hit.get("abstract") or "",
        "arxiv_id": (ext.get("ArXiv") or "").split("v")[0],
        "doi": ext.get("DOI") or "",
        "pdf_urls": [(hit.get("openAccessPdf") or {}).get("url") or ""],
    }


def _doi_from_crossref(title: str) -> str:
    data = _get_json(
        "https://api.crossref.org/works",
        params={"query.bibliographic": title, "rows": 3, "select": "DOI,title"},
    )
    for item in data.get("message", {}).get("items", []):
        if _normalise_title((item.get("title") or [""])[0]) == _normalise_title(title):
            return item.get("DOI", "")
    return ""


def _abstract_from_inverted_index(index: dict | None) -> str:
    if not index:
        return ""
    positions = [(pos, word) for word, poses in index.items() for pos in poses]
    return " ".join(word for _, word in sorted(positions))


def _from_openalex(doi: str) -> dict:
    data = _get_json(
        f"https://api.openalex.org/works/doi:{doi}",
        params={"select": "abstract_inverted_index,locations,best_oa_location"},
    )
    arxiv_id, pdf_urls = "", []
    for loc in [data.get("best_oa_location") or {}] + (data.get("locations") or []):
        landing = loc.get("landing_page_url") or ""
        match = re.search(r"arxiv\.org/abs/([\d.]+)", landing)
        if match:
            arxiv_id = match.group(1)
        if loc.get("pdf_url"):
            pdf_urls.append(loc["pdf_url"])
    return {
        "abstract": _abstract_from_inverted_index(data.get("abstract_inverted_index")),
        "arxiv_id": arxiv_id,
        "pdf_urls": pdf_urls,
    }


def _elsevier_article(doi: str) -> dict:
    """
    Elsevier Article Retrieval API (free key from dev.elsevier.com).
    Full text is returned for open-access articles and, on a subscribing
    institution's network, for subscribed ones; otherwise metadata/abstract.
    """
    data = _get_json(
        f"https://api.elsevier.com/content/article/doi/{doi}",
        params={"httpAccept": "application/json"},
        headers={"X-ELS-APIKey": config.ELSEVIER_API_KEY},
    ).get("full-text-retrieval-response", {})
    original = data.get("originalText")
    text = original if isinstance(original, str) else ""
    abstract = (data.get("coredata") or {}).get("dc:description") or ""
    return {"fulltext": clean_elsevier_text(text), "abstract": " ".join(abstract.split())}


_BACK_MATTER = re.compile(
    r"CRediT authorship|Declaration of competing interest|Declaration of Competing"
    r"|Acknowledge?ments?\b|Data availability|\bReferences\b"
)


def clean_elsevier_text(text: str) -> str:
    """
    Elsevier ``originalText`` is one flat string: metadata, image URLs and a
    table of contents, then the body, then back matter and references.
    Keep the body: last "1 Introduction" heading → first back-matter heading.
    """
    text = " ".join((text or "").split())
    starts = [m.start() for m in re.finditer(r"\b1\.?\s+Introduction\b", text)]
    if not starts:
        starts = [m.start() for m in re.finditer(r"\bIntroduction\b", text)][1:2]
    start = starts[-1] if starts else 0
    end_match = _BACK_MATTER.search(text, start + 5000)
    end = end_match.start() if end_match else len(text)
    return text[start:end].strip()


def enrich_metadata(paper: dict) -> dict:
    """
    Fill in what the feed did not provide. Results go into separate fields
    (``resolved_doi``, ``fulltext_arxiv_id``) so the paper's cache/feedback
    key never changes.
    """
    paper = dict(paper)
    pdf_urls = [paper.get("pdf_url") or ""]
    if paper.get("title") and (not paper.get("abstract") or not paper.get("arxiv_id")):
        try:
            _merge_found(paper, _from_semantic_scholar(paper), pdf_urls)
        except Exception:
            pass   # 无 key 时常被限流，后面还有 Crossref/OpenAlex

    if not _doi(paper) and paper.get("title"):
        try:
            paper["resolved_doi"] = _doi_from_crossref(paper["title"])
        except Exception:
            pass
    doi = _doi(paper)
    if doi and (not paper.get("abstract") or not paper.get("fulltext_arxiv_id")):
        try:
            _merge_found(paper, _from_openalex(doi), pdf_urls)
        except Exception:
            pass
    if doi.startswith("10.1016/") and config.ELSEVIER_API_KEY:
        try:
            found = _elsevier_article(doi)
            if found["fulltext"]:
                paper["_elsevier_fulltext"] = found["fulltext"]
            if found["abstract"] and not paper.get("abstract"):
                paper["abstract"] = found["abstract"]
        except Exception:
            pass

    paper["_pdf_urls"] = [u for u in dict.fromkeys(pdf_urls) if u]
    return paper


def _merge_found(paper: dict, found: dict, pdf_urls: list[str]) -> None:
    if found.get("abstract") and not paper.get("abstract"):
        paper["abstract"] = " ".join(found["abstract"].split())
    if (found.get("arxiv_id") and not paper.get("arxiv_id")
            and not paper.get("fulltext_arxiv_id")):
        paper["fulltext_arxiv_id"] = found["arxiv_id"]
    if found.get("doi") and not _doi(paper):
        paper["resolved_doi"] = found["doi"]
    pdf_urls.extend(found.get("pdf_urls") or [])


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


class _ArxivHTMLText(HTMLParser):
    """Extract readable body text from an arXiv (LaTeXML) HTML paper."""

    _SKIP = {"script", "style", "nav", "header", "footer", "button", "svg"}
    _BLOCK = {"p", "li", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6",
              "div", "section", "tr", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.math_depth = 0
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class") or ""
        skip = tag in self._SKIP or "ltx_bibliography" in classes
        if self.skip_depth or skip:
            if tag not in _VOID_TAGS:
                self.skip_depth += 1
            return
        if tag == "math":
            self.math_depth += 1
            self.parts.append(f" {attrs.get('alttext', '')} ")
            return
        if tag in _VOID_TAGS:
            return
        self.stack.append(tag)
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n## ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth -= 1
            return
        if tag == "math":
            self.math_depth = max(0, self.math_depth - 1)
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth and not self.math_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


_VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col"}


def _arxiv_html_text(arxiv_id: str) -> str:
    resp = requests.get(
        f"https://arxiv.org/html/{arxiv_id}",
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    if resp.status_code != 200 or "ltx_document" not in resp.text:
        return ""
    body = resp.text
    start = body.find("<article")
    if start != -1:
        body = body[start:]
    parser = _ArxivHTMLText()
    parser.feed(body)
    return parser.text()


def _drop_references(text: str) -> str:
    cut = re.search(r"\n\s*(References|REFERENCES|Bibliography)\s*\n", text or "")
    return (text[:cut.start()] if cut else text or "").strip()


def _pdf_text(url: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=90)
    if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
        return ""
    reader = PdfReader(io.BytesIO(resp.content))
    pages = [page.extract_text() or "" for page in reader.pages[:30]]
    return _drop_references("\n".join(pages))


def fetch_fulltext(paper: dict) -> tuple[str, str]:
    """Best-effort full text. Returns (text, where_from); ('', '') if none."""
    candidates = []
    arxiv_id = paper.get("arxiv_id") or paper.get("fulltext_arxiv_id")
    if arxiv_id:
        candidates.append(("arXiv", lambda: _arxiv_html_text(arxiv_id)))
        candidates.append(("arXiv PDF", lambda: _pdf_text(f"https://arxiv.org/pdf/{arxiv_id}")))
    if paper.get("_elsevier_fulltext"):
        candidates.append(("Elsevier API", lambda: paper["_elsevier_fulltext"]))
    for url in paper.get("_pdf_urls", []):
        candidates.append(("开放获取 PDF", lambda url=url: _pdf_text(url)))

    for source, get_text in candidates:
        try:
            text = get_text()
        except Exception:
            continue
        if len(text) > 3000:
            return text, source
    return "", ""


def split_chunks(text: str, chunk_chars: int, max_chunks: int) -> list[str]:
    """Split on paragraph boundaries into at most ``max_chunks`` chunks."""
    text = text[:chunk_chars * max_chunks]   # anything beyond is appendix
    chunks, current = [], ""
    for para in re.split(r"\n{2,}|\n(?=## )", text):
        while len(para) > chunk_chars:          # e.g. text without line breaks
            if current:
                chunks.append(current)
                current = ""
            cut = para.rfind(". ", chunk_chars // 2, chunk_chars) + 1 or chunk_chars
            chunks.append(para[:cut])
            para = para[cut:].lstrip()
        if len(current) + len(para) + 2 > chunk_chars and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current)
    return chunks[:max_chunks]


# ══════════════════════════════════════════════════════════════════════════════
#  LLM call
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是计算机视觉与农业机器人方向的研究助理。读者没有时间读原文，你的总结要让读者在 1 分钟内判断这篇论文值不值得读；不读原文也能掌握它的方法和结论。信息密度要高：多写具体的模块名、设计选择和数字，少写空泛的评价。

读者的研究方向（以下每一项都是读者的独立研究方向，推进其中任意一项的论文都与读者直接相关，不要只拿“苹果树剪枝”来衡量）：{profile}

请只依据用户提供的论文内容，输出一个 JSON 对象，字段如下（简体中文；方法名、数据集名、指标名保留英文）：
- "tldr": 一句话（不超过 70 字）：用什么方法解决了什么问题，最关键的一个数字结果
- "problem": 要解决的问题、为什么重要，以及现有方法的具体不足（2-3 句）
- "innovations": 字符串数组，3-5 条核心创新点，每条写清“做了什么 + 为什么有效”，具体到技术手段；不要写“提出了一种新方法”这类空话
- "method": 方法流程（4-6 句）：输入是什么 → 每个关键模块怎么做（网络结构、关键公式思想、损失函数、训练/推理细节）→ 输出是什么
- "experiments": 实验设置：数据集/采集平台与规模、对比基线、评价指标、硬件/运行速度
- "results": 主要定量结果，列出 2-4 个具体数字，并说明相对最强基线的提升幅度
- "ablation": 用 1-2 句写消融或分析实验的结论，例如去掉某模块后指标下降多少
- "limitations": 局限或存疑之处（原文承认的，或你从实验设置看出的不足，如数据规模小、场景单一、缺少某类对比）
- "resources": 写明代码/数据集/模型是否公开；原文给出了链接就原样附上，原文没有链接时绝不能自己编写链接
- "relevance": 先指出它属于读者的哪个方向，再说具体可以借鉴哪个模块/思路（例如能否用到果树枝干重建、跨模态匹配等场景）；不属于任何方向就直说“关系不大”
- "rating": 1-5 的整数，表示对这位读者的阅读价值：5=直接推进读者某个方向且方法新颖/效果显著；4=属于读者某个方向，值得细读；3=属于读者方向但创新有限，或相邻方向有可借鉴模块；2=只有零星可借鉴点；1=无关
- "rating_reason": 打分理由（一句话）

要求：内容中没有的信息写“未提及”，绝不编造数字；只输出 JSON。
如果内容依据是“仅标题”：只根据标题填写 tldr、relevance、rating、rating_reason，其余字段一律写“未提及”，不要猜测方法和结果。"""


NOTES_PROMPT = """你在帮研究者精读一篇论文。下面是论文《{title}》正文的第 {index}/{total} 部分。
请用中文要点提取这一部分中的具体信息（方法名、模块名、数据集名、指标名保留英文，数字原样保留）：
- 问题与动机、现有方法的不足
- 方法细节：整体流程、各模块做什么、关键设计/损失函数/训练方式
- 实验设置：数据集或采集平台、对比基线、评价指标、硬件
- 定量结果与消融实验结论（写出具体数字和对应的数据集/指标）
- 作者承认的局限
- 代码、数据集、项目主页链接（原样保留 URL）
本部分没有涉及的方面直接省略；不要评价，不要编造；不超过 800 字。"""


def _user_prompt(paper: dict, text: str, basis: str) -> str:
    return (
        f"标题：{paper.get('title', '')}\n"
        f"来源：{paper.get('venue') or paper.get('source', '')}\n"
        f"内容依据：{BASIS_LABELS[basis]}\n\n"
        f"论文内容：\n{text or '（无摘要，仅有标题）'}"
    )


def _call_llm(system: str, user: str, json_mode: bool = True) -> str:
    if config.SUMMARY_BACKEND == "gemini":
        return _call_gemini(system, user, json_mode)
    return _call_ollama(system, user, json_mode)


def _paper_content(paper: dict, fulltext: str) -> tuple[str, int]:
    """
    Text handed to the final summary call, and how many chunks were read.
    Short papers (or Gemini's long context) go in whole; long papers are
    read chunk by chunk and condensed to notes first.
    """
    chunk_chars = config.FULLTEXT_CHUNK_CHARS
    if config.SUMMARY_BACKEND == "gemini":
        chunk_chars = config.GEMINI_MAX_CHARS
    if len(fulltext) <= chunk_chars:
        return fulltext, 1

    chunks = split_chunks(fulltext, chunk_chars, config.FULLTEXT_MAX_CHUNKS)
    notes = []
    for i, chunk in enumerate(chunks, 1):
        print(f"        精读第 {i}/{len(chunks)} 段…", flush=True)
        prompt = NOTES_PROMPT.format(title=paper.get("title", ""), index=i, total=len(chunks))
        try:
            notes.append(f"【第 {i} 部分笔记】\n" + _call_llm(prompt, chunk, json_mode=False).strip())
        except Exception as e:
            notes.append(f"【第 {i} 部分】（读取失败：{e}）")
    content = "\n\n".join(notes)
    if paper.get("abstract"):
        content = f"【原文摘要】\n{paper['abstract']}\n\n{content}"
    return content, len(chunks)


def _call_ollama(system: str, user: str, json_mode: bool = True) -> str:
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_ctx": config.OLLAMA_NUM_CTX, "temperature": 0.2},
    }
    if json_mode:
        body["format"] = "json"
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json=body,
        timeout=900,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_gemini(system: str, user: str, json_mode: bool = True) -> str:
    from google import genai
    from google.genai import types

    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 未设置")
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if json_mode else "text/plain",
            temperature=0.2,
        ),
    )
    return resp.text or ""


def _flatten(value) -> str:
    """Models sometimes nest dicts/lists; turn them into readable Chinese text."""
    if isinstance(value, dict):
        return "；".join(f"{k}：{_flatten(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "、".join(_flatten(v) for v in value)
    return str(value or "").strip()


_ECHOED_PROMPT = re.compile(
    r"(代码、数据集、模型是否公开及链接|哪个模块贡献最大|有什么规律（给出数字）"
    r"|相对最强基线的提升幅度?|数据集/采集平台(?:与规模)?)\s*[:：]\s*"
)
_URL = re.compile(r"https?://[^\s，。；、）)\]]+")


def drop_unsupported_urls(summary: dict, source_text: str) -> dict:
    """Remove links the model wrote that do not appear in the paper text."""
    for key, value in summary.items():
        if not isinstance(value, str):
            continue
        for url in _URL.findall(value):
            if url.rstrip("./") not in source_text:
                value = value.replace(url, "").replace("：；", "；")
        cleaned = value.strip(" ：:；;，,")
        summary[key] = cleaned if cleaned else "未提及"
    return summary


def parse_summary(raw: str) -> dict:
    """Parse and normalise the model's JSON; never raises."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip()).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    try:
        data = json.loads(match.group(0) if match else cleaned)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (ValueError, json.JSONDecodeError):
        return {"tldr": cleaned[:300], "error": "模型未返回有效 JSON"}

    labels = dict(DETAIL_FIELDS)
    summary = {}
    for key in ("tldr", "problem", "method", "experiments", "results",
                "ablation", "limitations", "resources", "relevance", "rating_reason"):
        value = _ECHOED_PROMPT.sub("", _flatten(data.get(key, "")))
        label = labels.get(key)
        if label:
            value = re.sub(rf"^(?:{re.escape(label)}|主要定量结果)\s*[:：]\s*", "", value)
        summary[key] = value

    innovations = data.get("innovations", [])
    if isinstance(innovations, str):
        innovations = [s for s in re.split(r"[；;\n]+", innovations) if s.strip()]
    summary["innovations"] = [_flatten(s) for s in innovations if _flatten(s)]

    try:
        summary["rating"] = max(1, min(5, int(data.get("rating"))))
    except (TypeError, ValueError):
        summary["rating"] = None
    return summary


def summarise_paper(paper: dict) -> dict:
    """Return the paper with ``summary`` / ``summary_basis`` fields added."""
    if config.SUMMARY_BACKEND not in ("ollama", "gemini"):
        return paper

    paper = enrich_metadata(paper)
    text, basis, chunks, source, fulltext = "", "title", 0, "", ""
    if config.SUMMARY_USE_FULLTEXT:
        fulltext, source = fetch_fulltext(paper)
        if fulltext:
            text, chunks = _paper_content(paper, fulltext)
            basis = "fulltext"
    if not text and paper.get("abstract"):
        text, basis = paper["abstract"], "abstract"

    system = SYSTEM_PROMPT.format(profile=config.RESEARCH_PROFILE)
    user = _user_prompt(paper, text, basis)
    if chunks > 1:
        user += "\n\n（以上是逐段精读全文得到的笔记，请综合所有部分作答）"
    try:
        summary = parse_summary(_call_llm(system, user))
        source_text = user + (fulltext if basis == "fulltext" else "")
        summary = drop_unsupported_urls(summary, source_text)
    except Exception as e:
        summary = {"error": f"总结失败：{e}"}
    if basis == "title":
        # 只有标题时，模型写出的方法/结果都是猜测，不展示
        for key, _ in DETAIL_FIELDS:
            summary.pop(key, None)

    result = {k: v for k, v in paper.items() if not k.startswith("_")}
    result.update(summary=summary, summary_basis=basis)
    if source:
        result["fulltext_source"] = source
        result["fulltext_chunks"] = chunks
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Markdown rendering
# ══════════════════════════════════════════════════════════════════════════════

def basis_text(paper: dict) -> str:
    """e.g. "全文（arXiv，分 4 段精读）" / "摘要" / "仅标题"."""
    label = BASIS_LABELS.get(paper.get("summary_basis"), "")
    if paper.get("summary_basis") == "fulltext" and paper.get("fulltext_source"):
        chunks = paper.get("fulltext_chunks") or 1
        detail = paper["fulltext_source"]
        if chunks > 1:
            detail += f"，分 {chunks} 段精读"
        label += f"（{detail}）"
    return label


def stars(rating: int | None) -> str:
    if not rating:
        return "—"
    return "★" * rating + "☆" * (5 - rating)


def overview_line(paper: dict) -> str:
    """One-line Chinese gist used in the digest's overview table."""
    summary = paper.get("summary") or {}
    text = summary.get("tldr") if _informative(summary.get("tldr")) else ""
    text = text or paper.get("summary_zh") or paper.get("title", "")
    return text.replace("|", "/").replace("\n", " ")


def _informative(value) -> bool:
    if isinstance(value, list):
        return any(_informative(v) for v in value)
    text = str(value or "").strip().strip("。.")
    return bool(text) and text not in ("未提及", "无", "N/A")


def render_summary(paper: dict) -> list[str]:
    """Markdown lines for one paper's summary (empty when none exists)."""
    summary = paper.get("summary")
    if not summary:
        legacy = paper.get("summary_zh")
        return [f"**内容总结：** {legacy}", ""] if legacy else []

    lines = []
    if summary.get("error") and not summary.get("tldr"):
        return [f"> ⚠️ {summary['error']}", ""]
    if summary.get("rating"):
        reason = summary.get("rating_reason", "")
        lines.append(f"**推荐度：{stars(summary['rating'])}**　{reason}".rstrip())
        lines.append("")
    if _informative(summary.get("tldr")):
        lines += [f"**一句话：** {summary['tldr']}", ""]
    if _informative(summary.get("relevance")):
        lines += [f"**与我的研究：** {summary['relevance']}", ""]

    for key, label in DETAIL_FIELDS:
        value = summary.get(key)
        if not _informative(value):
            continue
        if isinstance(value, list):
            items = [item for item in value if _informative(item)]
            lines.append(f"**{label}：**")
            lines += [f"{i}. {item}" for i, item in enumerate(items, 1)]
            lines.append("")
        else:
            lines += [f"**{label}：** {value}", ""]
    if summary.get("error"):
        lines += [f"> ⚠️ {summary['error']}", ""]
    return lines
