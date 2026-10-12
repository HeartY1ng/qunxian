"""
check.py —— 体检：每个成员能不能用、思考档位是不是真的有效

为什么要有它：
  接一个新模型时，最常见的坑是：key 没配对、模型名写错、思考参数对方不支持（有的报错，有的默默忽略）。
  "默默忽略"最难发现，所以 --effort-test 会用同一道题分别跑"关 / 低 / 高"三档，
  比较思考用掉的 token 数，直接告诉你这个模型的档位是有效、只能开关、还是根本没反应。
"""

import shutil

from .callers import CallError, call_member
from .effort import cli_command

# 一道必须认真推理才能答全的题，用来比较不同档位的思考量。
# 题太简单的话（比如只问第一步），高档低档都不用多想，看不出差别——2026-10 实测踩过这个坑。
# 代价：思考多的模型（比如 DeepSeek）一次可能用掉上万 token，所以 --effort-test 是可选的。
PUZZLE = ("有 12 枚硬币，其中一枚重量不同（可能更重也可能更轻）。用天平最多称 3 次，"
          "找出这枚硬币并判断它更重还是更轻。给出完整称法，200 字以内。")


def reasoning_tokens(usage):
    """从用量里取思考 token 数。各家放的位置略有不同，取不到就返回 None。"""
    details = usage.get("completion_tokens_details") or {}
    return details.get("reasoning_tokens")


def check_member(member, say, effort_test=False):
    """检查一个成员，把结果一行行交给 say 打印。返回 True 表示能用。"""
    name = f"{member['name']}（{member['id']}）"
    if member["type"] == "host":
        say(f"⏭  {name}：host 成员由主席 agent 派出，这里不检查", f"⏭  {name}: host member, dispatched by the agent; skipped")
        return True

    # cli 成员先看命令装没装，省得白等
    if member["type"] == "cli":
        cmd, _ = cli_command(member, "/dev/null")
        if not shutil.which(cmd[0]):
            say(f"❌ {name}：找不到命令 {cmd[0]}", f"❌ {name}: command not found: {cmd[0]}")
            return False

    # 发一句很短的话，确认真能对话（思考型模型会先想一会儿，所以不限制输出长度）
    try:
        out = call_member(member, "Reply with exactly one word.", "Say OK.", _tmp_out(member), timeout=180)
    except CallError as e:
        say(f"❌ {name}：{e}", f"❌ {name}: {e}")
        return False
    rt = reasoning_tokens(out.get("usage", {}))
    extra = f"，思考 token {rt}" if rt is not None else ""
    say(f"✅ {name}：能用，回复 {out['text'][:20]!r}，{out['seconds']} 秒{extra}",
        f"✅ {name}: OK, replied {out['text'][:20]!r} in {out['seconds']}s" + (f", reasoning tokens {rt}" if rt is not None else ""))

    if effort_test:
        effort_report(member, name, say)
    return True


def effort_report(member, name, say):
    """用同一道题跑关 / 低 / 高三档，比较思考 token，给出结论。只对能拿到 token 数的 openai 成员有意义。"""
    if member["type"] != "openai":
        say(f"   {name}：命令行成员拿不到 token 数，无法自动比较档位",
            f"   {name}: CLI members report no token counts; effort can't be compared automatically")
        return
    counts = {}
    for level in ("off", "low", "high"):
        try:
            out = call_member({**member, "effort": level}, "Answer briefly.", PUZZLE, _tmp_out(member), timeout=600)
            counts[level] = reasoning_tokens(out.get("usage", {})) or 0
        except CallError as e:
            counts[level] = None
            say(f"   {level} 档报错：{e}", f"   level {level} failed: {e}")
    say(f"   思考 token：关={counts['off']}  低={counts['low']}  高={counts['high']}",
        f"   reasoning tokens: off={counts['off']}  low={counts['low']}  high={counts['high']}")

    # 判断规则：高档比低档多一半以上算"档位有效"；否则看"关"能不能把思考降到很低
    lo, hi, off = counts["low"], counts["high"], counts["off"]
    if lo and hi and hi > lo * 1.5:
        say("   结论：档位有效", "   verdict: effort levels work")
    elif off is not None and lo and off < lo * 0.3:
        say("   结论：只能开关思考，低/中/高差别不大（单次测试有随机波动，可多测几次）",
            "   verdict: on/off only; low/medium/high barely differ (single run, results vary)")
    else:
        say("   结论：看不出档位有效果。检查 effort_style 是否写对，或这个模型不支持调节",
            "   verdict: no visible effect. Check effort_style, or the model may not support it")


def _tmp_out(member):
    """cli 成员用 -o 写文件时需要一个临时路径；检查用完即丢。"""
    import tempfile
    from pathlib import Path
    return Path(tempfile.gettempdir()) / f"qunxian-check-{member['id']}.md"
