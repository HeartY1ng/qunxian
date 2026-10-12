"""
callers.py —— 真正去"叫"每个成员发言的地方

两种成员由这里调用：
  openai  任何兼容 OpenAI 格式的 HTTP 接口（DeepSeek、Kimi、千问、OpenAI、Gemini、Ollama……）
  cli     任何命令行程序（Codex、claude -p、Gemini CLI、自己写的脚本……）
第三种 host 成员由主席 agent 自己派子 agent 作答，不经过这里。

key 的读取顺序：环境变量（api_key_env 指定的名字）→ macOS 钥匙串（keychain 指定的条目名）。
key 只在内存里用，不写文件、不打印。只用 Python 标准库。
"""

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request

from .effort import cli_command, openai_body


class CallError(Exception):
    """调用某个成员失败。消息会写进这个成员本轮的发言文件，主席和看板都能看到。"""


def get_key(member):
    """
    取 openai 成员的 key。都找不到时说明该去哪里配置。
    本地模型（比如 Ollama）不需要 key：两个字段都不写就返回空字符串。
    """
    env_name = member.get("api_key_env")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    svc = member.get("keychain")
    if svc and platform.system() == "Darwin":
        # security 是 macOS 自带的钥匙串命令：-s 条目名，-a 账户名（可省略），-w 只输出密码本身
        args = ["security", "find-generic-password", "-s", svc]
        if member.get("keychain_account"):
            args += ["-a", member["keychain_account"]]
        args.append("-w")
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    if env_name or svc:
        where = " 或 ".join(x for x in [f"环境变量 {env_name}" if env_name else "",
                                         f"钥匙串条目 {svc}" if svc else ""] if x)
        raise CallError(f"找不到 {member['id']} 的 key，请设置{where}")
    return ""


def http_json(url, key, payload=None, timeout=60):
    """发一个 HTTP 请求并解析 JSON。payload 为 None 时是 GET（查模型列表），否则 POST。"""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"   # OpenAI 兼容接口通用的认证方式
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 把状态码和返回内容的前 300 字带出来，方便判断是 key 错、余额不足还是参数不支持
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise CallError(f"HTTP {e.code}：{body}") from None
    except urllib.error.URLError as e:
        raise CallError(f"连不上 {url}：{e.reason}") from None


def call_openai(member, system, user, timeout=900, max_tokens=None):
    """
    调用 openai 类型成员，返回 {"text", "model", "usage"}。
    请求体 = 模型 + 消息 + 思考档位对应的字段 + 成员配置里的 extra_body（每次都带的固定字段）。
    """
    payload = {"model": member["model"],
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    payload.update(openai_body(member, member.get("effort", "high")))
    payload.update(member.get("extra_body", {}))
    if max_tokens or member.get("max_tokens"):
        payload["max_tokens"] = max_tokens or member["max_tokens"]
    url = member["base_url"].rstrip("/") + "/chat/completions"
    result = http_json(url, get_key(member), payload, timeout=timeout)
    try:
        text = result["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError):
        raise CallError(f"返回格式看不懂：{json.dumps(result, ensure_ascii=False)[:300]}")
    return {"text": text, "model": result.get("model", member["model"]), "usage": result.get("usage", {})}


def call_cli(member, system, user, out_path, timeout=900, cwd=None):
    """
    调用 cli 类型成员：把"规则 + 本轮任务"从标准输入喂给命令，
    回答从标准输出读（output=stdout），或者从命令写出的文件读（output=file，比如 codex 的 -o）。
    命令不经过 shell 执行，参数里有空格、引号都不会出问题。
    """
    cmd, output = cli_command(member, out_path)
    try:
        r = subprocess.run(cmd, input=system + "\n\n" + user, text=True, capture_output=True,
                           timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        raise CallError(f"找不到命令 {cmd[0]}，请确认已安装并在 PATH 里")
    except subprocess.TimeoutExpired:
        raise CallError(f"超过 {timeout} 秒没有回答")
    if r.returncode != 0:
        # 报错信息优先看标准错误，没有就看标准输出，各取最后 300 字（最后几行最有用）
        msg = (r.stderr.strip() or r.stdout.strip())[-300:]
        raise CallError(f"命令返回错误码 {r.returncode}：{msg}")
    if output == "file":
        text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    else:
        text = r.stdout
    if not text.strip():
        raise CallError("命令运行成功，但没有输出任何内容")
    return {"text": text.strip(), "model": member.get("model", ""), "usage": {}}


def call_member(member, system, user, out_path, timeout=900, cwd=None):
    """按成员类型分发，并记下耗时。host 成员不该走到这里。"""
    start = time.time()
    if member["type"] == "openai":
        out = call_openai(member, system, user, timeout=timeout)
    elif member["type"] == "cli":
        out = call_cli(member, system, user, out_path, timeout=timeout, cwd=cwd)
    else:
        raise CallError(f"{member['id']} 是 host 成员，需要由主席 agent 派子 agent 作答")
    out["seconds"] = round(time.time() - start, 1)
    return out
