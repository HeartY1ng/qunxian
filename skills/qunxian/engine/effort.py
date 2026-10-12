"""
effort.py —— 把统一的五档思考程度，翻译成每个成员自己听得懂的参数

为什么需要它：
  各家模型调"想多久"的方式完全不一样（2026-10 实测）：
    - Kimi：接受 reasoning_effort（low/medium/high，有效），也能用 thinking 开关关掉思考
    - DeepSeek：thinking 开关有效；reasoning_effort 接口接受，但实测几乎没影响
    - Codex 命令行：-c model_reasoning_effort=档位
    - Claude 命令行：--effort 档位
  用户只需要记"关/低/中/高/最高"五个字，翻译交给这里。
  遇到没见过的模型，用户在配置里写 effort_body / effort_values 就能自己适配，不用改代码。
"""

# Codex 和 Claude 两种常用命令行的现成配置（"预设"），成员写 "preset": "codex" 就能直接用
CLI_PRESETS = {
    "codex": {
        # exec：非交互模式；-s read-only：只读沙盒，不让它改文件；--ephemeral：不留会话记录；
        # -o {out}：把最终回答写进文件
        "command": ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "--ephemeral", "-o", "{out}"],
        "model_args": ["-m", "{model}"],
        "effort_args": ["-c", "model_reasoning_effort={effort}"],
        "effort_values": {"off": "low", "low": "low", "medium": "medium", "high": "high", "max": "xhigh"},
        "output": "file",
    },
    "claude": {
        # -p：非交互，打印回答；--tools ""：不给任何工具，只让它说话；不保存会话
        "command": ["claude", "-p", "--tools", "", "--no-session-persistence"],
        "model_args": ["--model", "{model}"],
        "effort_args": ["--effort", "{effort}"],
        "effort_values": {"off": "low", "low": "low", "medium": "medium", "high": "high", "max": "max"},
        "output": "stdout",
    },
}

# host 成员（由主席 agent 自己派子 agent）用的档位叫法，按 Claude Code 子 agent 的 effort 参数来
HOST_EFFORT_VALUES = {"off": "low", "low": "low", "medium": "medium", "high": "high", "max": "max"}

# openai 类型、写了 effort_style 含 "effort" 时 reasoning_effort 的默认取值
OPENAI_EFFORT_VALUES = {"off": "low", "low": "low", "medium": "medium", "high": "high", "max": "high"}


def openai_body(member, level):
    """
    openai 类型成员：根据档位，算出请求里要额外加的字段。
    优先级：effort_body（用户逐档写死的字段） > effort_style（几种常见方式）。

    effort_style 的取值：
      effort         只发 reasoning_effort
      toggle         只发 thinking 开关：关 → disabled，其他档 → enabled
      effort+toggle  关 → 关掉思考；其他档 → 开启思考 + reasoning_effort
      none（默认）   什么都不发，用接口自己的默认
    """
    custom = member.get("effort_body") or {}
    if level in custom:
        return dict(custom[level])

    style = member.get("effort_style", "none")
    values = {**OPENAI_EFFORT_VALUES, **member.get("effort_values", {})}
    body = {}
    if style in ("toggle", "effort+toggle"):
        body["thinking"] = {"type": "disabled" if level == "off" else "enabled"}
    # effort+toggle 在"关"档已经关掉思考了，就不再发档位
    if style == "effort" or (style == "effort+toggle" and level != "off"):
        body["reasoning_effort"] = values[level]
    return body


def cli_spec(member):
    """cli 类型成员：取它的命令配置。写了 preset 就从预设起步，成员自己写的字段可以覆盖预设。"""
    base = dict(CLI_PRESETS.get(member.get("preset"), {}))
    for key in ("command", "model_args", "effort_args", "output"):
        if key in member:
            base[key] = member[key]
    base["effort_values"] = {**base.get("effort_values", {}), **member.get("effort_values", {})}
    return base


def cli_command(member, out_path):
    """
    拼出 cli 成员要执行的完整命令（一个列表，不经过 shell，避免特殊字符出问题）。
    命令里的 {model} {effort} {out} 三个占位符会被替换。没写模型就不加模型参数。
    """
    spec = cli_spec(member)
    level = member.get("effort", "high")
    effort_value = spec.get("effort_values", {}).get(level, level)
    values = {"model": member.get("model", ""), "effort": effort_value, "out": str(out_path)}

    def fill(args):
        return [a.replace("{model}", values["model"]).replace("{effort}", values["effort"])
                 .replace("{out}", values["out"]) for a in args]

    cmd = fill(spec.get("command", []))
    if member.get("model") and spec.get("model_args"):
        cmd += fill(spec["model_args"])
    if spec.get("effort_args"):
        cmd += fill(spec["effort_args"])
    return cmd, spec.get("output", "stdout")


def describe(member):
    """给人看的一句话：这个成员用什么模型、什么档位。看板和日志里用。"""
    model = member.get("model") or member.get("preset") or member["type"]
    return f"{model} · {member.get('effort', 'high')}"
