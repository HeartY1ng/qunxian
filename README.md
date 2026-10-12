# 群贤 Qunxian

> 群贤毕至。让多个大模型围绕你的问题开一场圆桌会：**独立作答 → 匿名互评 → 最终立场**，最后由主席写一份综合报告。

[English](README.en.md)

![群贤看板](docs/board.png)

## 为什么要让好几个模型一起讨论

- **一个模型的盲点是固定的。** 让同一个模型扮演几个角色，"分歧"常常是演出来的。不同公司的模型训练数据、偏好不同，才更可能出现真正的不同意见。
- **模型爱附和。** 自由聊天时，第一个发言的观点会把后面的人带偏，最后变成"一致同意"。所以群贤先让大家**独立作答**，再**匿名**互相批评（看不到对方是哪家），最后各自给出**最终立场**。每场还会随机抽一个成员当**反方**，专门挑刺。
- **结论要能追溯。** 报告里每个观点都标明来自谁，所有原始发言都保留，你可以自己对照检查。

## 特点

| | |
|---|---|
| **成员不限** | 任何兼容 OpenAI 格式的接口（DeepSeek、Kimi、千问、智谱、OpenAI、Gemini、Ollama 本地模型……）、任何命令行工具（Codex、`claude -p`、你自己的脚本），或者由主席 agent 自己派出的子 agent。想加几个加几个。 |
| **主席不限** | Claude Code、Codex，或者任何能运行命令的 agent 都能当主席；没有 agent 也行，终端里一条命令跑完。 |
| **思考程度统一五档** | 关 / 低 / 中 / 高 / 最高，自动翻译成每家自己的参数。`check --effort-test` 会实测某个模型的档位到底有没有效果。 |
| **实时看板** | 每轮谁在发言、谁交卷了、用了多久、花了多少 token，点开就能看全文。 |
| **零依赖** | 只用 Python 3.9+ 标准库，不需要 `pip install` 任何东西。 |
| **隐私** | key 只从环境变量或 macOS 钥匙串读取，不写进任何文件；看板只监听本机。 |

## 快速开始

```bash
git clone https://github.com/HeartY1ng/qunxian.git
cd qunxian
./qunxian init          # 生成配置文件 ~/.qunxian/config.json
```

打开配置文件，删掉你没有的成员，设置好 key（见下文"配置成员"），然后：

```bash
./qunxian check                          # 检查每个成员能不能用
./qunxian run "你的问题"                  # 一条命令开完整场会
./qunxian board --open                   # 另开一个终端，打开实时看板
```

报告在 `~/.qunxian/records/<日期-标题>/report.md`。

## 在 agent 里用（推荐）

让 agent 当主席，体验最好：它会补充你的背景、派出自己的子 agent 参加讨论、最后写报告。技能文件 `skills/qunxian/SKILL.md` 遵循通用的技能格式，**Claude Code 和 Codex 用的是同一份**。

**Claude Code**：在 Claude Code 的对话框里输入

```
/plugin install qunxian --marketplace HeartY1ng/qunxian
```

**Codex**：把技能文件夹链接到 Codex 的技能目录

```bash
ln -s "$PWD/skills/qunxian" ~/.codex/skills/qunxian
```

**其他 agent**（Cursor、Gemini CLI……）：在项目的 `AGENTS.md` / `GEMINI.md` 里加一句：

```
当用户要求用群贤讨论某个问题时，读 <仓库路径>/skills/qunxian/SKILL.md 并照着做。
```

然后直接说："用群贤讨论一下：……"，也可以加上"快速"、"只要 DeepSeek 和 Kimi"、"思考程度最高"之类的要求。

## 配置成员

配置文件是 JSON，`members` 里每一项是一个成员，数量不限。成员有三种类型：

| type | 是什么 | 必填 |
|---|---|---|
| `openai` | 任何兼容 OpenAI 格式的 HTTP 接口 | `base_url`、`model`；key 用 `api_key_env`（环境变量名）或 `keychain`（macOS 钥匙串条目名） |
| `cli` | 任何命令行：问题从标准输入传入，回答从标准输出读 | `preset`（内置 `codex`、`claude`），或自己写 `command` |
| `host` | 由主席 agent 自己派出的子 agent（比如在 Claude Code 里派 Claude） | 无；只在 agent 当主席时有效，`run` 命令会自动跳过 |

```json
{
  "language": "zh",
  "chair": "deepseek",
  "members": [
    { "id": "claude",   "name": "Claude",   "type": "host", "model": "opus" },
    { "id": "gpt",      "name": "GPT",      "type": "cli",  "preset": "codex" },
    { "id": "deepseek", "name": "DeepSeek", "type": "openai",
      "base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro", "cheap_model": "deepseek-flash",
      "api_key_env": "DEEPSEEK_API_KEY", "effort_style": "toggle" },
    { "id": "local",    "name": "本地 Qwen", "type": "openai",
      "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:14b" }
  ]
}
```

其他常用字段：`keychain_account`（钥匙串条目的账户名，存的时候用了 `-a` 才需要）、`cheap_model`（"快速"预设时换用的便宜模型）、`effort`（这个成员默认的思考档位）、`enabled: false`（先不参加）、`color`（看板头像颜色）、`extra_body`（每次请求都附带的字段）、`max_tokens`。

`chair` 指定命令行模式下由哪个成员写报告。它可以是任何 `openai` 或 `cli` 成员，不必参加讨论。

### 思考程度

统一用五档：`关 / 低 / 中 / 高 / 最高`（也可以写 `off / low / medium / high / max`）。每个 `openai` 成员用 `effort_style` 说明自己支持哪种调法：

| effort_style | 发送什么 | 适用 |
|---|---|---|
| `effort` | `reasoning_effort` | OpenAI 等支持档位的接口 |
| `toggle` | `thinking: {type: enabled/disabled}` | 只能开关思考的接口 |
| `effort+toggle` | "关"档关掉思考，其他档开启思考并带上 `reasoning_effort` | 两种都支持的接口 |
| `none`（默认） | 什么都不发 | 不支持调节的接口 |

遇到特殊的接口，可以用 `effort_values` 改档位的取值（比如把"最高"映射成 `xhigh`），或者用 `effort_body` 逐档写死要发送的字段：

```json
"effort_body": { "off": {"thinking": {"type": "disabled"}}, "max": {"thinking": {"type": "enabled", "budget_tokens": 32000}} }
```

**不确定某个模型的档位有没有用？跑一下：**

```bash
./qunxian check <成员id> --effort-test
```

它会用同一道推理题分别跑"关 / 低 / 高"三档，比较思考 token 数，并给出结论。我们 2026 年 10 月的实测结果：

| 模型 | 关 | 低 | 高 | 结论 |
|---|---|---|---|---|
| Kimi（kimi-k3） | 0 | 525 | 871 | 档位有效 → `effort+toggle` |
| DeepSeek（deepseek-v4-pro） | 0 | 约 1.9 万 | 约 2.2 万 | 只能开关 → `toggle`；想更快更省，请换用 `deepseek-flash` |

### 预设

| 预设 | 轮数 | 思考 | 其他 |
|---|---|---|---|
| 快速（quick） | 1 | 低 | 有 `cheap_model` 就换用便宜模型 |
| 标准（standard，默认） | 3 | 高 | |
| 深度（deep） | 3 | 最高 | 最慢、最贵，留给重大决策 |

可以在配置的 `presets` 里改写，或者加自己的预设。开会时也可以临时指定：`--preset 快速 --members a,b --effort 中 --rounds 2`。

### 数据放在哪

配置和会议记录放在"数据文件夹"里，按以下顺序查找：

1. 环境变量 `QUNXIAN_HOME`
2. 从当前目录往上找到的第一个 `.qunxian/` 文件夹（`./qunxian init --here` 会在当前目录建一个，适合每个项目一套配置）
3. `~/.qunxian/`

## 一场会议的文件

```
records/2026-10-12-085739-AI时代还要学编程吗/
  meta.json      话题、成员（模型、档位）、匿名对照表、反方、主席、每次调用的耗时和用量
  r1/ r2/ r3/    prompt-<成员>.md 是发给成员的提示词，<成员>.md 是成员的发言
  report.md      综合报告
```

示例：[一场真实的会议报告](docs/example-report.md)。

## 局限，先说清楚

- **主席有偏向。** 主席也是一个模型，可能偏向与自己同源的成员（比如 Claude 当主席时偏向 Claude）。报告里会写明主席是谁，原始发言都保留，方便你对照检查。
- **成本随人数和轮数增长。** 第 2、3 轮每个成员都要读所有人的发言，超过 6 人时会明显变慢变贵。思考很多的模型（比如 DeepSeek）一轮可能用掉上万 token。
- **成员不查资料**，只凭自己的知识回答。需要最新信息的问题，请把资料写进话题里。
- **走订阅的命令行成员**（Codex、`claude -p`）拿不到 token 数，看板上只显示耗时。
- **host 成员需要主席 agent 有派子 agent 的能力。** 做不到时，建会议时把它排除掉即可。

## 测试

```bash
python3 -m unittest discover tests
```

测试用"假成员"跑完整流程，不联网，不花钱。

## 卸载

1. 删掉仓库文件夹
2. 删掉数据文件夹（`~/.qunxian/`，或项目里的 `.qunxian/`）
3. Claude Code：`/plugin uninstall qunxian`；Codex：`rm ~/.codex/skills/qunxian`
4. 如果把 key 存进了钥匙串：`security delete-generic-password -s <条目名>`

群贤不会改动你电脑上的其他任何东西。

## 许可证

[MIT](LICENSE)
