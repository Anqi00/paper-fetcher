#!/usr/bin/env python3
"""
Local web UI for the daily paper digests.

  .venv/bin/python ui.py            # then open http://127.0.0.1:8765
  .venv/bin/python ui.py --port 9000 --output-dir /path/to/digests

Features: browse digests, read structured summaries, toggle “不感兴趣”
(writes output/disliked.txt), summarise old digests, run today's fetch.
Only listens on 127.0.0.1; uses the Python standard library only.
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import feedback
import summarizer

ROOT = Path(__file__).parent
INDEX_HTML = ROOT / "ui" / "index.html"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Job:
    """At most one background fetcher.py run at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.label = ""
        self.log_path: Path | None = None
        self.started = ""

    def status(self) -> dict:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            code = None if self.process is None or running else self.process.returncode
            log = ""
            if self.log_path and self.log_path.exists():
                log = self.log_path.read_text("utf-8", errors="replace")[-6000:]
            return {"running": running, "label": self.label, "exit_code": code,
                    "started": self.started, "log": log}

    def start(self, label: str, args: list[str], output_dir: Path) -> bool:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return False
            output_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = output_dir / "ui-job.log"
            log = self.log_path.open("w", encoding="utf-8")
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "fetcher.py"),
                 "--output-dir", str(output_dir), *args],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            )
            log.close()
            self.label = label
            self.started = datetime.now().strftime("%H:%M:%S")
            return True


def list_digests(output_dir: Path) -> list[dict]:
    digests = []
    for path in sorted(output_dir.glob("*.json"), reverse=True):
        if not DATE_RE.fullmatch(path.stem):
            continue
        try:
            papers = json.loads(path.read_text("utf-8"))["papers"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        ratings = [(p.get("summary") or {}).get("rating") or 0 for p in papers]
        digests.append({
            "date": path.stem,
            "count": len(papers),
            "summarized": any(p.get("summary") for p in papers),
            "best": max(ratings, default=0),
        })
    return digests


def load_digest(output_dir: Path, date_str: str) -> dict:
    payload = json.loads((output_dir / f"{date_str}.json").read_text("utf-8"))
    disliked = feedback.disliked_entries(output_dir)
    for i, paper in enumerate(payload["papers"], 1):
        paper["index"] = i
        paper["disliked"] = (date_str, i) in disliked
        paper["basis_text"] = summarizer.basis_text(paper) if paper.get("summary_basis") else ""
    payload["detail_fields"] = summarizer.DETAIL_FIELDS
    return payload


def make_handler(output_dir: Path, job: Job):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/digests":
                self._json({"digests": list_digests(output_dir),
                            "today": datetime.now().strftime("%Y-%m-%d"),
                            "backend": config.SUMMARY_BACKEND})
            elif path.startswith("/api/digest/"):
                date_str = path.rsplit("/", 1)[-1]
                if not DATE_RE.fullmatch(date_str):
                    return self._json({"error": "日期格式错误"}, 400)
                try:
                    self._json(load_digest(output_dir, date_str))
                except (OSError, ValueError, KeyError):
                    self._json({"error": f"找不到 {date_str} 的日报"}, 404)
            elif path == "/api/job":
                self._json(job.status())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            # Reject cross-site form posts: only same-origin JSON requests.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._json({"error": "需要 JSON 请求"}, 415)
            body = self._body()
            if self.path == "/api/dislike":
                date_str, index = str(body.get("date", "")), body.get("index")
                if not DATE_RE.fullmatch(date_str) or not isinstance(index, int) or index < 1:
                    return self._json({"error": "参数错误"}, 400)
                feedback.set_dislike(output_dir, date_str, index, bool(body.get("disliked")))
                return self._json({"ok": True})
            if self.path == "/api/run":
                action = body.get("action")
                if action == "fetch":
                    started = job.start("抓取今日日报", [], output_dir)
                elif action == "resummarize" and DATE_RE.fullmatch(str(body.get("date", ""))):
                    started = job.start(f"总结 {body['date']}",
                                        ["--resummarize", body["date"]], output_dir)
                else:
                    return self._json({"error": "未知操作"}, 400)
                if not started:
                    return self._json({"error": "已有任务在运行"}, 409)
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="论文日报本地界面")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    output_dir = (Path(args.output_dir) if args.output_dir
                  else ROOT / config.OUTPUT_DIR).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(output_dir, Job()))
    url = f"http://127.0.0.1:{args.port}"
    print(f"论文日报界面：{url}   （Ctrl+C 退出）")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
