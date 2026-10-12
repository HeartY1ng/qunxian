#!/usr/bin/env python3
"""
qunxian.py —— 群贤：让多个大模型围绕一个问题讨论，再由主席写综合报告

这是命令行入口，只用 Python 标准库（3.9 及以上），不需要安装任何依赖。
任何 agent（Claude Code、Codex、Cursor、Gemini CLI……）只要能运行命令，都能当主席；
没有 agent 也行，直接在终端用 run 命令跑完整场会议。

常用命令：
  init [--here]                   生成配置文件（--here 放在当前项目的 .qunxian/ 里）
  check [成员id...] [--effort-test] 检查成员能不能用、思考档位是否有效
  run "话题" [选项]                一条命令跑完整场会议（不含 host 成员），并由配置里的主席成员写报告
  board [--port 8788] [--open]    打开实时看板
  list                            列出所有会议

给 agent 主席用的分步命令：
  new "短标题" "话题" [选项]       建会议，第一行输出会议文件夹
  prompts <会议> <轮次>            生成本轮提示词，输出 host 成员的任务（JSON）
  call <会议> <轮次>               并行调用本轮所有非 host 成员
  report <会议> --print [--chair 名字]   输出写报告的完整指令和材料，由 agent 自己写报告
  report <会议> [--chair-member id]      由配置里的某个成员写报告

开会选项：--preset 快速/标准/深度  --members a,b,c  --effort 关/低/中/高/最高  --rounds 1-3
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

# 让 Python 找得到同一文件夹下的 engine 包（不管从哪个目录运行这个脚本）
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import meeting                                   # noqa: E402
from engine.callers import CallError, call_member            # noqa: E402
from engine.check import check_member                        # noqa: E402
from engine.config import (ConfigError, PALETTE, find_home,  # noqa: E402
                           load_config, norm_level, resolve_settings)

LANG = "zh"   # 输出提示用的语言，读到配置后会更新


def say(zh, en=None):
    """按配置语言打印一句话；没给英文就都用中文。"""
    print(en if (LANG == "en" and en) else zh, flush=True)


def settings_from(args, cfg, cli_mode=False):
    """把命令行上的开会选项交给 resolve_settings，合成最终设置。"""
    members = args.members.split(",") if getattr(args, "members", None) else None
    return resolve_settings(cfg, preset=args.preset, members=members, effort=args.effort,
                            rounds=args.rounds, cli_mode=cli_mode)


def read_topic(args):
    """话题可以直接写在命令里，也可以用 --topic-file 从文件读（话题很长时更方便）。"""
    if getattr(args, "topic_file", None):
        return Path(args.topic_file).read_text(encoding="utf-8").strip()
    return args.topic


# ---------------- 各个子命令 ----------------

def cmd_init(args):
    """把示例配置复制成 config.json。已经有配置时不覆盖，除非加 --force。"""
    home = find_home(create_here=args.here)
    target = home / "config.json"
    if target.exists() and not args.force:
        say(f"配置已存在：{target}（要重新生成请加 --force）", f"Config already exists: {target} (use --force to overwrite)")
        return
    home.mkdir(parents=True, exist_ok=True)
    example = "members.example.en.json" if args.lang == "en" else "members.example.json"
    shutil.copy(HERE / example, target)
    say(f"已生成配置：{target}\n下一步：打开它，删掉你没有的成员、填好 key 的位置，然后运行 qunxian check",
        f"Created config: {target}\nNext: edit it (remove members you don't have, set key locations), then run: qunxian check")


def cmd_check(args, home, cfg):
    """逐个检查成员。不写 id 就检查所有成员。"""
    wanted = set(args.ids) if args.ids else None
    ok = True
    for i, m in enumerate(cfg["members"]):
        if wanted and m["id"] not in wanted:
            continue
        # 检查时用成员自己配置的档位，没写就用"高"
        m = {**m, "name": m.get("name", m["id"]), "effort": norm_level(m.get("effort")) or "high",
             "color": m.get("color", PALETTE[i % len(PALETTE)])}
        ok = check_member(m, say, effort_test=args.effort_test) and ok
    sys.exit(0 if ok else 1)


def cmd_new(args, home, cfg):
    """建会议。第一行只输出会议文件夹路径，方便 agent 读取。"""
    s = settings_from(args, cfg)
    m = meeting.new_meeting(home, args.title, read_topic(args), s, chair=args.chair)
    print(m)
    meta = meeting.load_meta(m)
    names = {x["id"]: x["name"] for x in meta["members"]}
    devil = names.get(meta["devil"], "—")
    say(f"成员：{'、'.join(names.values())}；{s['rounds']} 轮；预设 {s['preset']}；反方：{devil}",
        f"Members: {', '.join(names.values())}; {s['rounds']} rounds; preset {s['preset']}; devil's advocate: {devil}")


def cmd_prompts(args, home, cfg):
    """生成第 N 轮提示词，输出 host 成员的任务清单（JSON），agent 据此派子 agent。"""
    tasks = meeting.build_prompts(Path(args.meeting), args.round)
    print(json.dumps(tasks, ensure_ascii=False, indent=2))


def cmd_call(args, home, cfg):
    """并行调用第 N 轮所有非 host 成员，每个成员结束时打印一行。"""
    def done(x, rec):
        if rec["ok"]:
            say(f"✅ {x['name']}：{rec['seconds']} 秒", f"✅ {x['name']}: {rec['seconds']}s")
        else:
            say(f"❌ {x['name']}：{rec['error']}", f"❌ {x['name']}: {rec['error']}")
    meeting.call_round(Path(args.meeting), args.round, on_done=done)


def chair_writes(m, cfg, member_id=None):
    """
    命令行模式：由配置里的主席成员（config.json 的 chair 字段，或 --chair-member）写报告。
    主席成员可以是任何 openai / cli 成员，不必参加讨论。
    """
    cid = member_id or cfg.get("chair")
    if not cid:
        say("没有指定主席成员（config.json 里写 \"chair\": \"成员id\"），跳过报告。"
            "可以运行 report <会议> --print 把材料交给你的 agent 来写。",
            "No chair member set (add \"chair\": \"<member id>\" to config.json); skipping report.")
        return None
    chair = next((x for x in cfg["members"] if x["id"] == cid), None)
    if not chair or chair["type"] == "host":
        say(f"主席成员 {cid} 不存在，或是 host 类型（命令行模式下没人能派它）",
            f"Chair member {cid} not found or is a host member")
        return None
    chair = {**chair, "name": chair.get("name", cid), "effort": norm_level(chair.get("effort")) or "high"}
    prompt = meeting.report_prompt(m, chair=f"{chair['name']}（{chair.get('model') or chair.get('preset')}）")
    say(f"主席 {chair['name']} 正在写报告…", f"Chair {chair['name']} is writing the report…")
    sys_prompt = "你是圆桌讨论的主席。" if LANG == "zh" else "You are the chair of a roundtable discussion."
    try:
        out = call_member(chair, sys_prompt, prompt, m / "report.tmp.md", timeout=1800, cwd=m)
    except CallError as e:
        say(f"❌ 主席写报告失败：{e}", f"❌ Chair failed: {e}")
        return None
    # 命令行主席如果是 codex 这类"写文件"的成员，会留下临时文件，用完删掉
    tmp = m / "report.tmp.md"
    if tmp.exists():
        tmp.unlink()
    (m / "report.md").write_text(out["text"] + "\n", encoding="utf-8")
    return m / "report.md"


def cmd_report(args, home, cfg):
    """--print：输出指令和材料给 agent；否则由主席成员写。"""
    m = Path(args.meeting)
    if args.print:
        print(meeting.report_prompt(m, chair=args.chair))
        return
    path = chair_writes(m, cfg, args.chair_member)
    if path:
        say(f"报告：{path}", f"Report: {path}")


def cmd_run(args, home, cfg):
    """一条命令跑完整场：建会议 → 每轮生成提示词并调用 → 主席写报告。host 成员自动跳过。"""
    s = settings_from(args, cfg, cli_mode=True)
    if s["skipped"]:
        say(f"⏭  跳过 host 成员（命令行模式下没有 agent 派它们）：{', '.join(s['skipped'])}",
            f"⏭  Skipping host members (no agent to dispatch them): {', '.join(s['skipped'])}")
    topic = read_topic(args)
    title = args.title or topic.strip().splitlines()[0][:20]
    m = meeting.new_meeting(home, title, topic, s, chair=None)
    say(f"会议文件夹：{m}\n看板：qunxian board --open", f"Meeting folder: {m}\nBoard: qunxian board --open")
    for r in range(1, s["rounds"] + 1):
        say(f"— 第 {r} 轮 —", f"— Round {r} —")
        meeting.build_prompts(m, r)
        args.meeting, args.round = str(m), r
        cmd_call(args, home, cfg)
    if not args.no_report:
        path = chair_writes(m, cfg)
        if path:
            say(f"\n报告：{path}", f"\nReport: {path}")


def cmd_list(args, home, cfg):
    """列出所有会议：时间、标题、有没有报告。"""
    rec = home / "records"
    for m in sorted(rec.iterdir() if rec.exists() else [], reverse=True):
        if (m / "meta.json").exists():
            meta = meeting.load_meta(m)
            mark = "✓" if (m / "report.md").exists() else "…"
            print(f"{mark} {meta['created'][:16].replace('T', ' ')}  {meta['title']}  {m}")


def cmd_board(args, home, cfg):
    """启动看板（只读，只监听 127.0.0.1）。"""
    from engine import board
    board.serve(home, port=args.port, open_browser=args.open)


# ---------------- 命令行参数 ----------------

def build_parser():
    p = argparse.ArgumentParser(prog="qunxian", description="群贤：多模型圆桌讨论 / multi-model roundtable")
    sub = p.add_subparsers(dest="cmd", required=True)

    def meeting_opts(sp):
        """开会相关的通用选项，new 和 run 都要用。"""
        sp.add_argument("--preset", help="快速 / 标准 / 深度（quick / standard / deep）")
        sp.add_argument("--members", help="只让这些成员参加，逗号分隔的 id")
        sp.add_argument("--effort", help="所有成员统一用这一档：关 / 低 / 中 / 高 / 最高")
        sp.add_argument("--rounds", type=int, help="轮数 1～3")
        sp.add_argument("--topic-file", help="从文件读话题")

    sp = sub.add_parser("init", help="生成配置文件")
    sp.add_argument("--here", action="store_true", help="放在当前目录的 .qunxian/ 里")
    sp.add_argument("--force", action="store_true", help="覆盖已有配置")
    sp.add_argument("--lang", choices=["zh", "en"], default="zh", help="配置示例的语言")

    sp = sub.add_parser("check", help="检查成员")
    sp.add_argument("ids", nargs="*")
    sp.add_argument("--effort-test", action="store_true", help="比较关/低/高三档的思考量")

    sp = sub.add_parser("new", help="建会议（agent 主席用）")
    sp.add_argument("title")
    sp.add_argument("topic", nargs="?", default="")
    sp.add_argument("--chair", help="主席的名字，写进报告的偏向声明")
    meeting_opts(sp)

    for name in ("prompts", "call"):
        sp = sub.add_parser(name, help="生成提示词" if name == "prompts" else "调用非 host 成员")
        sp.add_argument("meeting")
        sp.add_argument("round", type=int)

    sp = sub.add_parser("report", help="写报告")
    sp.add_argument("meeting")
    sp.add_argument("--print", action="store_true", help="输出指令和材料，由 agent 写")
    sp.add_argument("--chair", help="主席名字（配合 --print）")
    sp.add_argument("--chair-member", help="用这个成员写报告")

    sp = sub.add_parser("run", help="一条命令跑完整场")
    sp.add_argument("topic", nargs="?", default="")
    sp.add_argument("--title")
    sp.add_argument("--no-report", action="store_true")
    meeting_opts(sp)

    sp = sub.add_parser("board", help="打开看板")
    sp.add_argument("--port", type=int, default=8788)
    sp.add_argument("--open", action="store_true", help="自动打开浏览器")

    sub.add_parser("list", help="列出会议")
    return p


def main():
    global LANG
    args = build_parser().parse_args()
    try:
        if args.cmd == "init":
            return cmd_init(args)
        home = find_home()
        cfg = load_config(home)
        LANG = cfg.get("language", "zh")
        handler = {"check": cmd_check, "new": cmd_new, "prompts": cmd_prompts, "call": cmd_call,
                   "report": cmd_report, "run": cmd_run, "list": cmd_list, "board": cmd_board}[args.cmd]
        if args.cmd in ("new", "run") and not (args.topic or args.topic_file):
            raise ConfigError("没有话题：直接写在命令里，或用 --topic-file 指定文件")
        handler(args, home, cfg)
    except ConfigError as e:
        print(f"⚠️  {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
