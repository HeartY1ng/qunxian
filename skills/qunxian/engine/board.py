"""
board.py —— 实时看板的服务端（只用 Python 标准库）

干什么：
  开一个只有本机能访问的网页（默认 127.0.0.1:8788），显示会议进展：每轮每个成员的状态、
  耗时、字数、发言全文，以及综合报告；可以切换历史会议。成员数量不限。
  网页每 2 秒问一次"现在什么状况"，服务端每次都现读会议文件夹，不需要数据库。

为什么这么设计：
  - 只读：看板只看文件，不改任何东西，开着关着都不影响会议本身。
  - 只监听 127.0.0.1：别的设备连不上；又因为没有任何"写"接口，所以不需要令牌。

接口：
  /                      网页
  /api/meetings          所有会议（新的在前）
  /api/state?m=文件夹名   某次会议的完整状态；不带 m 就是最新一次
"""

import json
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .meeting import FAILED

PAGE = Path(__file__).resolve().parent.parent / "board" / "index.html"


def meetings(home):
    """所有会议文件夹，新的在前（文件夹名以日期时间开头，倒序即按时间倒序）。"""
    rec = home / "records"
    if not rec.exists():
        return []
    return sorted((p for p in rec.iterdir() if (p / "meta.json").exists()), reverse=True)


def cell(m, rnd, mid):
    """
    某成员某一轮的状态，全靠看文件判断：
      没有提示词 → waiting（这一轮还没开始）
      有提示词、没发言 → running
      发言以 [FAILED] 开头 → failed
      有发言 → done
    """
    d = m / f"r{rnd}"
    out = d / f"{mid}.md"
    if not (d / f"prompt-{mid}.md").exists():
        return {"state": "waiting", "text": ""}
    if not out.exists() or out.stat().st_size == 0:
        return {"state": "running", "text": ""}
    text = out.read_text(encoding="utf-8")
    if text.startswith(FAILED):
        return {"state": "failed", "text": text[len(FAILED):].strip()}
    return {"state": "done", "text": text}


def snapshot(home, name=None):
    """收集一次会议的全部状态给网页用。name 为空时取最新一次。"""
    ms = meetings(home)
    if not ms:
        return {"meeting": None}
    m = next((p for p in ms if p.name == name), ms[0])
    meta = json.loads((m / "meta.json").read_text(encoding="utf-8"))

    # 外部调用的耗时和用量，按"轮次-成员"索引；同时按成员累加
    calls = {f"{c['round']}-{c['member']}": c for c in meta.get("calls", [])}
    totals = {x["id"]: {"seconds": 0.0, "tokens": 0} for x in meta["members"]}
    for c in meta.get("calls", []):
        if c["member"] in totals:
            totals[c["member"]]["seconds"] += c.get("seconds") or 0
            totals[c["member"]]["tokens"] += (c.get("usage") or {}).get("total_tokens", 0)

    rounds = []
    for r in range(1, meta.get("rounds", 3) + 1):
        row = {}
        for x in meta["members"]:
            st = cell(m, r, x["id"])
            row[x["id"]] = {**st, "seconds": calls.get(f"{r}-{x['id']}", {}).get("seconds"), "chars": len(st["text"])}
        rounds.append(row)

    report = m / "report.md"
    finished = (datetime.fromtimestamp(report.stat().st_mtime).isoformat(timespec="seconds")
                if report.exists() else None)
    return {"meeting": m.name, "title": meta["title"], "topic": meta["topic"], "language": meta.get("language", "zh"),
            "preset": meta.get("preset"), "members": meta["members"], "labels": meta["labels"],
            "devil": meta.get("devil"), "chair": meta.get("chair"), "created": meta["created"],
            "finished": finished, "totals": totals, "rounds": rounds,
            "report": report.read_text(encoding="utf-8") if report.exists() else None}


def meeting_list(home):
    """给下拉框用：文件夹名、标题、开始时间、是否已出报告。"""
    out = []
    for m in meetings(home):
        meta = json.loads((m / "meta.json").read_text(encoding="utf-8"))
        out.append({"id": m.name, "title": meta["title"], "created": meta["created"],
                    "done": (m / "report.md").exists()})
    return out


def serve(home, port=8788, open_browser=False):
    """启动看板。端口被占用时自动往后找一个空的。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/api/state":
                body = json.dumps(snapshot(home, parse_qs(url.query).get("m", [None])[0]), ensure_ascii=False)
                self._send(body.encode(), "application/json; charset=utf-8")
            elif url.path == "/api/meetings":
                self._send(json.dumps(meeting_list(home), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            elif url.path == "/":
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")   # 不缓存，保证每次都是最新状态
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass   # 每 2 秒一次的请求不打印，免得刷屏

    # 只绑定 127.0.0.1：只有本机能访问。端口被占就试下一个，最多试 20 个
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"端口 {port}～{port + 19} 都被占用了")
    url = f"http://127.0.0.1:{p}"
    print(f"看板 / Board: {url}  （数据：{home}）", flush=True)
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()
