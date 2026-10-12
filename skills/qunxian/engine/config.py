"""
config.py —— 找数据文件夹、读配置、选成员

干什么：
  - 决定"数据文件夹"在哪：配置文件 config.json 和会议记录 records/ 都放在里面。
  - 读配置，把开会时的选项（预设、成员、思考档位）整理成最终要用的设置。

为什么这么设计：
  - 数据和程序分开放：程序（技能文件夹）升级时会被整个替换，数据放在别处才不会丢。
  - 数据文件夹按这个顺序找，越靠前越优先：
      1. 环境变量 QUNXIAN_HOME 指定的文件夹
      2. 从当前目录往上逐级找，第一个名叫 .qunxian 的文件夹（这样每个项目可以有自己的一套）
      3. 用户主目录下的 ~/.qunxian
  - 思考程度统一成五档（关/低/中/高/最高），各家模型的叫法由 effort.py 翻译。
"""

import json
import os
from pathlib import Path

# 五档思考程度的标准写法（程序内部统一用英文）
LEVELS = ["off", "low", "medium", "high", "max"]

# 允许用户用的别名：中文、以及各家常见叫法，都折算成标准写法
LEVEL_ALIASES = {
    "关": "off", "低": "low", "中": "medium", "高": "high", "最高": "max",
    "none": "off", "minimal": "low", "xhigh": "max", "ultra": "max",
}

# 预设的别名：中文名 → 英文名
PRESET_ALIASES = {"快速": "quick", "标准": "standard", "深度": "deep"}

# 配置文件里没写 presets 时用这三套
DEFAULT_PRESETS = {
    "quick":    {"rounds": 1, "effort": "low",  "cheap": True},   # 1 轮、低思考、有便宜模型就用便宜的
    "standard": {"rounds": 3, "effort": "high"},                  # 3 轮、高思考
    "deep":     {"rounds": 3, "effort": "max"},                   # 3 轮、最高思考
}

# 每轮字数上限的默认值：第 1 轮把观点讲全，后两轮聚焦
DEFAULT_WORD_LIMITS = [800, 600, 600]

# 没给成员指定颜色时，按顺序从这里取（看板上的头像颜色）
PALETTE = ["#d97757", "#10a37f", "#4d6bfe", "#8b5cf6", "#e5484d", "#0ea5e9",
           "#d4a017", "#db2777", "#14b8a6", "#64748b"]


class ConfigError(Exception):
    """配置有问题时抛出，命令行会把说明打印给用户，而不是一大段报错堆栈。"""


def find_home(create_here=False):
    """
    找数据文件夹。create_here=True 时（init --here）直接用当前目录下的 .qunxian。
    返回 Path，不保证文件夹已存在（init 命令负责创建）。
    """
    if create_here:
        return Path.cwd() / ".qunxian"
    env = os.environ.get("QUNXIAN_HOME")
    if env:
        return Path(env).expanduser()
    # 从当前目录往上一级一级找 .qunxian 文件夹
    for d in [Path.cwd(), *Path.cwd().parents]:
        if (d / ".qunxian").is_dir():
            return d / ".qunxian"
    return Path.home() / ".qunxian"


def load_config(home):
    """读 config.json。文件不存在时给出明确的下一步提示。"""
    path = home / "config.json"
    if not path.exists():
        raise ConfigError(f"找不到配置文件 {path}。先运行：qunxian init（或 init --here 放在当前项目里）\n"
                          f"Config not found at {path}. Run: qunxian init")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置文件不是合法的 JSON：{path} 第 {e.lineno} 行：{e.msg}")
    validate(cfg)
    return cfg


def validate(cfg):
    """检查配置里最容易写错的地方：成员 id 重复、类型不认识、缺必填字段。"""
    members = cfg.get("members", [])
    if not members:
        raise ConfigError("配置里没有任何成员（members 为空）")
    seen = set()
    for m in members:
        mid = m.get("id")
        if not mid:
            raise ConfigError(f"有成员没写 id：{m}")
        if mid in seen:
            raise ConfigError(f"成员 id 重复：{mid}")
        seen.add(mid)
        t = m.get("type")
        if t not in ("openai", "cli", "host"):
            raise ConfigError(f"成员 {mid} 的 type 必须是 openai / cli / host 之一，现在是 {t!r}")
        if t == "openai" and not (m.get("base_url") and m.get("model")):
            raise ConfigError(f"成员 {mid}（openai 类型）必须写 base_url 和 model")
        if t == "cli" and not (m.get("preset") or m.get("command")):
            raise ConfigError(f"成员 {mid}（cli 类型）必须写 preset（codex / claude）或 command")


def norm_level(level):
    """把用户写的思考档位折算成标准写法；写错时报出可选值。"""
    if level is None:
        return None
    lv = LEVEL_ALIASES.get(str(level).strip(), str(level).strip().lower())
    if lv not in LEVELS:
        raise ConfigError(f"不认识的思考档位：{level}。可选：关/低/中/高/最高 或 {'/'.join(LEVELS)}")
    return lv


def resolve_settings(cfg, preset=None, members=None, effort=None, rounds=None, cli_mode=False):
    """
    把"配置文件 + 这次开会的临时选项"合成最终设置。优先级：临时选项 > 预设 > 配置默认值。

    参数：
      preset   预设名（快速/标准/深度 或 quick/standard/deep），不填用配置里的 default_preset
      members  只让这些成员参加（id 列表），不填就是所有 enabled 不为 false 的成员
      effort   强制所有成员用这一档思考，不填就用成员自己的 effort 或预设的 effort
      rounds   临时改轮数（1～3）
      cli_mode 为 True 表示没有 agent 主席（qunxian run），host 成员只能跳过
    返回：dict，包含 preset、rounds、word_limits、language、members（每个成员已算好 model 和 effort）、skipped
    """
    presets = {**DEFAULT_PRESETS, **cfg.get("presets", {})}
    pname = PRESET_ALIASES.get(preset, preset) if preset else cfg.get("default_preset", "standard")
    if pname not in presets:
        raise ConfigError(f"不认识的预设：{preset}。可选：{', '.join(presets)}")
    p = presets[pname]

    n_rounds = int(rounds or p.get("rounds", 3))
    if not 1 <= n_rounds <= 3:
        raise ConfigError("轮数只能是 1～3")

    forced = norm_level(effort)
    all_members = [m for m in cfg["members"] if m.get("enabled", True)]
    if members:
        wanted = [x.strip() for x in members if x.strip()]
        known = {m["id"] for m in cfg["members"]}
        unknown = [x for x in wanted if x not in known]
        if unknown:
            raise ConfigError(f"没有这些成员：{', '.join(unknown)}。已配置的有：{', '.join(sorted(known))}")
        all_members = [m for m in cfg["members"] if m["id"] in wanted]

    chosen, skipped = [], []
    for i, m in enumerate(all_members):
        # 命令行模式没人能派 host 成员，只能跳过，并告诉用户
        if cli_mode and m["type"] == "host":
            skipped.append(m["id"])
            continue
        m = dict(m)   # 复制一份再改，不动原配置
        m["effort"] = forced or norm_level(m.get("effort")) or norm_level(p.get("effort", "high"))
        # 快速预设：成员有便宜模型就换成便宜模型
        if p.get("cheap") and m.get("cheap_model"):
            m["model"] = m["cheap_model"]
        m.setdefault("name", m["id"])
        m.setdefault("color", PALETTE[i % len(PALETTE)])
        chosen.append(m)

    if len(chosen) < 2:
        raise ConfigError(f"至少要 2 个成员才能讨论，现在只有 {len(chosen)} 个"
                          + (f"（跳过了只能由 agent 派出的 host 成员：{', '.join(skipped)}）" if skipped else ""))

    limits = cfg.get("word_limits", DEFAULT_WORD_LIMITS)
    return {
        "preset": pname,
        "rounds": n_rounds,
        "word_limits": (list(limits) + DEFAULT_WORD_LIMITS)[:3],
        "language": cfg.get("language", "zh"),
        "devil": cfg.get("devil", True),
        "timeout": int(cfg.get("timeout", 900)),
        "members": chosen,
        "skipped": skipped,
    }
