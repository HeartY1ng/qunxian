"""
meeting.py —— 一场会议从开始到出报告的全部流程

流程：
  new     建会议文件夹，给成员发匿名编号，随机抽一个反方
  prompts 按模板生成第 N 轮每个成员的提示词（第 2、3 轮会带上前面几轮所有人的匿名发言）
  call    并行调用第 N 轮所有非 host 成员，发言写进 rN/<成员id>.md
  report  把三轮发言整理成材料，交给主席写综合报告（主席可以是 agent，也可以是配置里的某个成员）

会议文件夹的样子：
  records/2026-10-12-075433-标题/
    meta.json        话题、成员快照（模型、档位）、匿名对照表、反方、每次调用的耗时和用量
    r1/ r2/ r3/      prompt-<id>.md 是发给成员的提示词，<id>.md 是成员的发言
    report.md        综合报告

为什么这么设计：
  - 提示词由程序按模板生成，每个成员拿到的材料完全一样，主席没法偷偷引导。
  - 第 2、3 轮互相看到的发言是匿名的，编号每次随机打乱，避免"这是某大厂模型说的"带来的品牌偏见。
  - 所有中间文件都保留，任何人都能对照原始发言检查主席的汇总。
"""

import json
import random
import re
import threading
from datetime import datetime
from pathlib import Path

from .callers import CallError, call_member
from .effort import HOST_EFFORT_VALUES

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
FAILED = "[FAILED]"   # 调用失败时发言文件以这个开头，看板和主席据此识别；中英文通用
SEP = "\n\n=====\n\n"  # 提示词文件里"规则"和"本轮任务"的分隔线


def load_meta(m):
    """读会议的 meta.json。"""
    return json.loads((m / "meta.json").read_text(encoding="utf-8"))


def save_meta(m, meta):
    """写 meta.json。ensure_ascii=False 让中文直接可读。"""
    (m / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def label_for(i, lang):
    """第 i 个匿名编号：中文"成员A"，英文"Member A"。"""
    letter = chr(ord("A") + i)
    return f"成员{letter}" if lang == "zh" else f"Member {letter}"


def new_meeting(home, title, topic, settings, chair=None):
    """
    建一个新会议，返回会议文件夹路径。
    settings 来自 config.resolve_settings：成员（已算好模型和档位）、轮数、语言等。
    chair 是主席的名字（比如 "Claude（Claude Code）"），报告里会据此声明可能的偏向。
    """
    slug = re.sub(r'[\\/:*?"<>|\s]+', "-", title).strip("-")[:30] or "meeting"
    m = home / "records" / f"{datetime.now():%Y-%m-%d-%H%M%S}-{slug}"
    m.mkdir(parents=True, exist_ok=False)

    members = settings["members"]
    order = [x["id"] for x in members]
    random.shuffle(order)                     # 打乱后按顺序发编号，编号和模型无关
    labels = {mid: label_for(i, settings["language"]) for i, mid in enumerate(order)}
    devil = random.choice(order) if settings["devil"] else None

    # 成员快照：只存看板和报告需要的信息，绝不存 key
    snapshot = [{k: x.get(k) for k in ("id", "name", "type", "model", "effort", "color", "preset")}
                for x in members]
    (m / "topic.md").write_text(topic, encoding="utf-8")
    save_meta(m, {
        "title": title, "topic": topic, "language": settings["language"],
        "preset": settings["preset"], "rounds": settings["rounds"], "word_limits": settings["word_limits"],
        "timeout": settings["timeout"], "members": snapshot, "member_configs": strip_secrets(members),
        "labels": labels, "devil": devil, "chair": chair,
        "created": datetime.now().isoformat(timespec="seconds"), "calls": [],
    })
    return m


def strip_secrets(members):
    """
    保存一份完整的成员配置，后面每轮调用时用（这样中途改了 config.json 也不影响正在开的会）。
    配置里本来就不该有 key，这里再保险一下：字段名就叫 api_key / token / secret / password 的一律不存。
    （只拦整个字段名，避免误伤 max_tokens、api_key_env 这类正常字段）
    """
    bad = re.compile(r"^(api_?key|key|token|secret|password)$", re.I)
    return [{k: v for k, v in x.items() if not bad.match(k)} for x in members]


def template(lang, name, **values):
    """读某种语言的模板，把 {占位符} 换成内容。用 replace 而不是 format，内容里有大括号也没事。"""
    text = (PROMPTS / lang / name).read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def collect(m, rnd, meta, me=None, reveal=False):
    """
    把第 rnd 轮所有成员的发言拼成一段材料。
      me      标出"这是你自己"的那个成员（发给成员时用）
      reveal  True 时同时写出真实名字（给主席写报告用），False 时只用匿名编号
    按匿名编号排序，所有人看到的顺序都一样。
    """
    zh = meta["language"] == "zh"
    names = {x["id"]: x["name"] for x in meta["members"]}
    parts = []
    no_answer = "（本轮未发言）" if zh else "(no answer)"
    for mid in sorted(meta["labels"], key=lambda k: meta["labels"][k]):
        f = m / f"r{rnd}" / f"{mid}.md"
        text = f.read_text(encoding="utf-8").strip() if f.exists() else no_answer
        # 调用失败的报错不能当成"发言"传给其他成员；只有主席（reveal）看得到失败原因
        if text.startswith(FAILED):
            text = (no_answer + "\n\n" + text) if reveal else no_answer
        head = meta["labels"][mid]
        if reveal:
            head += f"（{names[mid]}）" if zh else f" ({names[mid]})"
        if mid == me:
            head += "（你自己）" if zh else " (you)"
        parts.append(f"### {head}\n\n{text}")
    return "\n\n".join(parts)


def build_prompts(m, rnd):
    """
    生成第 rnd 轮每个成员的提示词，写到 r{rnd}/prompt-<id>.md。
    返回 host 成员的任务清单（主席 agent 要据此派子 agent）：提示词路径、答案路径、模型、档位。
    """
    meta = load_meta(m)
    lang = meta["language"]
    d = m / f"r{rnd}"
    d.mkdir(exist_ok=True)
    host_tasks = []
    for x in meta["members"]:
        mid = x["id"]
        role = template(lang, "role_devil.md") if mid == meta["devil"] else ""
        system = template(lang, "system.md", label=meta["labels"][mid], role=role,
                          limit=meta["word_limits"][rnd - 1], n=len(meta["members"]))
        values = {"topic": meta["topic"]}
        if rnd >= 2:
            values["round1"] = collect(m, 1, meta, me=mid)
        if rnd >= 3:
            values["round2"] = collect(m, 2, meta, me=mid)
        (d / f"prompt-{mid}.md").write_text(system + SEP + template(lang, f"round{rnd}.md", **values),
                                            encoding="utf-8")
        if x["type"] == "host":
            host_tasks.append({"id": mid, "name": x["name"], "prompt": str(d / f"prompt-{mid}.md"),
                               "answer": str(d / f"{mid}.md"), "model": x.get("model"),
                               "effort": HOST_EFFORT_VALUES.get(x.get("effort", "high"), "high")})
    return host_tasks


def call_round(m, rnd, on_done=None):
    """
    并行调用第 rnd 轮所有非 host 成员。每个成员一个线程，总耗时约等于最慢的那一个。
    某个成员失败不影响别人：失败原因写进它的发言文件（以 [FAILED] 开头）。
    on_done(成员, 结果记录) 在每个成员结束时被调用，命令行用它实时打印进度。
    """
    meta = load_meta(m)
    configs = {x["id"]: x for x in meta["member_configs"]}
    lock = threading.Lock()

    def worker(x):
        prompt = (m / f"r{rnd}" / f"prompt-{x['id']}.md").read_text(encoding="utf-8")
        system, user = prompt.split(SEP, 1)
        out_file = m / f"r{rnd}" / f"{x['id']}.md"
        rec = {"round": rnd, "member": x["id"]}
        try:
            out = call_member(configs[x["id"]], system, user, out_file, timeout=meta["timeout"], cwd=m)
            out_file.write_text(out["text"], encoding="utf-8")
            rec.update(ok=True, seconds=out["seconds"], model=out.get("model"), usage=out.get("usage", {}))
        except CallError as e:
            out_file.write_text(f"{FAILED} {e}", encoding="utf-8")
            rec.update(ok=False, error=str(e)[:300])
        except Exception as e:   # 兜底：任何意外都不能让整场会崩掉
            out_file.write_text(f"{FAILED} {type(e).__name__}: {e}", encoding="utf-8")
            rec.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
        with lock:   # 多个线程同时改 meta 会互相覆盖，用锁保证一次只有一个在改
            meta["calls"].append(rec)
            save_meta(m, meta)
        if on_done:
            on_done(x, rec)

    threads = [threading.Thread(target=worker, args=(x,)) for x in meta["members"] if x["type"] != "host"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return meta["calls"]


def usage_summary(meta):
    """汇总用量：每个成员的总耗时和总 token；走订阅的命令行成员没有 token 数。"""
    zh = meta["language"] == "zh"
    lines = []
    for x in meta["members"]:
        calls = [c for c in meta["calls"] if c["member"] == x["id"] and c.get("ok")]
        secs = sum(c.get("seconds") or 0 for c in calls)
        tokens = sum((c.get("usage") or {}).get("total_tokens", 0) for c in calls)
        if x["type"] == "host":
            lines.append(f"- {x['name']}：" + ("由主席派出，不计时" if zh else "dispatched by the chair, not timed"))
        else:
            tok = f"，{tokens} token" if tokens else ""
            what = x.get("model") or x.get("preset") or x["type"]   # 没写模型名的命令行成员显示类型
            lines.append(f"- {x['name']}（{what}，{x['effort']}）：{secs:.0f} 秒{tok}"
                         if zh else f"- {x['name']} ({what}, {x['effort']}): {secs:.0f}s"
                         + (f", {tokens} tokens" if tokens else ""))
    return "\n".join(lines)


def report_prompt(m, chair=None):
    """
    生成"写综合报告"的完整指令 + 材料。agent 当主席时打印给 agent 看；
    命令行模式下发给配置里指定的主席成员。
    """
    meta = load_meta(m)
    if chair:
        meta["chair"] = chair
        save_meta(m, meta)
    zh = meta["language"] == "zh"
    names = {x["id"]: x["name"] for x in meta["members"]}
    roster = "\n".join(f"- {meta['labels'][x['id']]} = {x['name']}（{x.get('model') or x.get('preset') or x['type']}，"
                       f"{'思考' if zh else 'effort'} {x['effort']}）" for x in meta["members"])
    transcript = "\n\n".join(
        (f"## 第 {r} 轮\n\n" if zh else f"## Round {r}\n\n") + collect(m, r, meta, reveal=True)
        for r in range(1, meta["rounds"] + 1))
    nobody = "无" if zh else "none"
    return template(meta["language"], "report.md", topic=meta["topic"], roster=roster,
                    devil=names.get(meta["devil"], nobody), chair=meta.get("chair") or nobody,
                    rounds=meta["rounds"], transcript=transcript, usage=usage_summary(meta),
                    report_path=str(m / "report.md"))
