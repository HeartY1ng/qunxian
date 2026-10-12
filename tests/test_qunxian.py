"""
群贤的自动测试：不联网、不花钱。
用几个"假成员"（小 Python 命令，收到问题就回固定文字）跑完整流程，检查：
  - 思考档位的翻译是否正确
  - 配置选项（预设、成员筛选、档位别名）是否生效
  - 一整场会议能跑完，失败成员不会把报错当发言传给别人
运行：python3 -m unittest discover tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "skills" / "qunxian"
sys.path.insert(0, str(ENGINE))

from engine.config import ConfigError, resolve_settings     # noqa: E402
from engine.effort import cli_command, openai_body           # noqa: E402


def fake(name, reply):
    """造一个假的命令行成员：读完标准输入，打印 reply。"""
    return {"id": name, "name": name, "type": "cli",
            "command": [sys.executable, "-c", f"import sys;sys.stdin.read();print({reply!r})"]}


class EffortTest(unittest.TestCase):
    """思考档位翻译"""

    def test_toggle(self):
        m = {"effort_style": "toggle"}
        self.assertEqual(openai_body(m, "off"), {"thinking": {"type": "disabled"}})
        self.assertEqual(openai_body(m, "high"), {"thinking": {"type": "enabled"}})

    def test_effort_plus_toggle(self):
        m = {"effort_style": "effort+toggle"}
        self.assertEqual(openai_body(m, "off"), {"thinking": {"type": "disabled"}})
        self.assertEqual(openai_body(m, "medium"), {"thinking": {"type": "enabled"}, "reasoning_effort": "medium"})

    def test_custom_body_wins(self):
        m = {"effort_style": "effort", "effort_body": {"max": {"budget": 9}}}
        self.assertEqual(openai_body(m, "max"), {"budget": 9})
        self.assertEqual(openai_body(m, "low"), {"reasoning_effort": "low"})

    def test_codex_preset(self):
        cmd, output = cli_command({"preset": "codex", "model": "m1", "effort": "max",
                                   "effort_values": {"max": "ultra"}}, "/tmp/o.md")
        self.assertEqual(output, "file")
        self.assertIn("-o", cmd)
        self.assertEqual(cmd[-4:], ["-m", "m1", "-c", "model_reasoning_effort=ultra"])


class SettingsTest(unittest.TestCase):
    """配置合成"""

    cfg = {"members": [fake("a", "x"), fake("b", "y"),
                       {"id": "c", "type": "openai", "base_url": "u", "model": "big", "cheap_model": "small"},
                       {"id": "h", "type": "host"}]}

    def test_quick_preset_uses_cheap_model(self):
        s = resolve_settings(self.cfg, preset="快速")
        self.assertEqual(s["rounds"], 1)
        c = next(m for m in s["members"] if m["id"] == "c")
        self.assertEqual((c["model"], c["effort"]), ("small", "low"))

    def test_cli_mode_skips_host(self):
        s = resolve_settings(self.cfg, cli_mode=True)
        self.assertEqual(s["skipped"], ["h"])

    def test_effort_alias_and_errors(self):
        s = resolve_settings(self.cfg, effort="最高", members=["a", "b"])
        self.assertTrue(all(m["effort"] == "max" for m in s["members"]))
        with self.assertRaises(ConfigError):
            resolve_settings(self.cfg, members=["a"])          # 只有 1 人
        with self.assertRaises(ConfigError):
            resolve_settings(self.cfg, effort="超高")          # 不认识的档位


class EndToEndTest(unittest.TestCase):
    """用 run 命令跑完整场会议"""

    def test_run(self):
        with tempfile.TemporaryDirectory() as home:
            cfg = {"language": "zh", "chair": "chair", "members": [
                fake("a", "甲的观点"), fake("b", "乙的观点"),
                {"id": "bad", "type": "cli", "command": [sys.executable, "-c", "import sys;sys.exit(3)"]},
                {**fake("chair", "# 报告\n结论：OK"), "enabled": False}]}
            Path(home, "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            env = {**os.environ, "QUNXIAN_HOME": home}
            r = subprocess.run([sys.executable, str(ROOT / "qunxian"), "run", "测试话题", "--title", "测试"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            m = next(Path(home, "records").iterdir())
            self.assertTrue((m / "report.md").read_text(encoding="utf-8").startswith("# 报告"))
            # 第 2 轮发给 a 的材料里，失败成员显示为"未发言"，不能出现报错原文
            r2 = (m / "r2" / "prompt-a.md").read_text(encoding="utf-8")
            self.assertIn("（本轮未发言）", r2)
            self.assertNotIn("[FAILED]", r2)
            meta = json.loads((m / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(len(meta["calls"]), 9)            # 3 个成员 × 3 轮
            self.assertNotIn("chair", meta["labels"])          # 主席不参加讨论


if __name__ == "__main__":
    unittest.main()
