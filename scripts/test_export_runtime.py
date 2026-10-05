r"""
Runtime Export V1 回归测试（stdlib unittest，无新框架；全部确定性，不依赖墙钟/网络）。

覆盖（独立审核 blocker 4）：
  future locked exclusion / unlocked acceptance
  gate: 缺 data-unlock、坏时间、无时区 → fail closed（INVALID）
  切片时间不可解析 → 不入 runtime、不得成为 current_state anchor
  persona_state 不可辨 → null（绝不合成）
  05/06 排除；World Info 运维元规则小节排除
  manifest sha256/bytes；snapshot 内 ID 唯一（含 validate 显式断言）
  ADDENDUM：概要行优先且不截断；回退 标题 @ 场景；气象行 → world.weather（缺失 null）
运行：python scripts/test_export_runtime.py
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_runtime as er  # noqa: E402
from sync_gdrive import JST  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=JST)

CHRONICLE = """# 卷

## **【2026-10-01 10:00 JST | 已解锁切片】**
* **核心场景**: [[Livehouse RiNG]] 地下排练室
* **客观事件脉络**:
  全员按各自节奏推进周末日程。

## **【2027-01-01 00:00 JST | 未来切片】**
未来正文 SECRET-FUTURE-BODY

## **【坏时间 | 病理切片】**
病理正文

## **【2026-10-02 10:00 JST | 门控未解】**
<div class="time-gate" data-unlock="2027-01-01T00:00:00+09:00">

<div class="time-gate-badge">🔒 星轨观测锁定</div>

锁定正文

</div>

## **【2026-10-03 10:00 JST | 门控已解】**
<div class="time-gate" data-unlock="2026-10-03T00:00:00+09:00">

<div class="time-gate-badge">🔒 星轨观测锁定</div>

* **核心场景**: [[羽丘女子学园]]中庭
* **气象**: 秋阳清澄，气温回升至19℃
* **概要**: 超长概要文本前缀铺垫。""" + "填充文本确保超过二百字符。" * 20 + """此句必须完整保留于 recent_events 摘要之中。

</div>

## **【2026-10-04 10:00 JST | 门控缺属性】**
<div class="time-gate">

正文

</div>

## **【2026-10-05 10:00 JST | 门控坏时间】**
<div class="time-gate" data-unlock="not-a-time">

正文

</div>

## **【2026-10-06 10:00 JST | 门控无时区】**
<div class="time-gate" data-unlock="2026-10-06T00:00:00">

正文

</div>
""" + "\n"

WORLD_INFO = """# 乐团世界书

## **1\\. 世界观核心基准与演进公约 (Worldview Canon)**
世界基准正文，涉及 [[丰川祥子]]。

## **【置顶全局核心准则速查 / Quick Reference Core Directives】**
1. **共享日记公约**：每人每天最多读写一次共享日记（运维元规则）。

## **2\\. 核心公共载体与资产协议 (Shared Artifacts & Assets)**
- **使用规范**：每人每天最多翻阅并写入一次（维护配额）。
- **记忆持久化机制**：跨会话持久化绑定说明（运维元规则）。
- 世界内概念：乐团共享日记本由成员传阅书写。

## **3\\. 地理空间与生活据点拓扑 (Key Locations)**
地理拓扑正文。

## **6\\. 世界书更新与维护公约 (Maintenance Protocol)**
维护公约正文（运维元规则）。
""" + "\n"

DIARY = """# 乐团共享日记本

### 【2026-10-01 21:00 JST | [[丰川祥子]]】
今日日记正文。
""" + "\n"

ARCHIVE_WHITE = """# 丰川祥子档案

## 5\\. 当前形态状态机 (Current Persona State)

4. **1\\. 当前激活状态**: 白祥 ([[月之森女子学园|月之森]] / 纯澈澄澈)
""" + "\n"


def make_content(root, archive=ARCHIVE_WHITE):
    j = lambda *p: os.path.join(root, *p)
    os.makedirs(j("01-乐团世界书"), exist_ok=True)
    os.makedirs(j("02-主世界编年史"), exist_ok=True)
    os.makedirs(j("03-乐团共享日记"), exist_ok=True)
    os.makedirs(j("04-成员记忆档案"), exist_ok=True)
    os.makedirs(j("05-创作者随想"), exist_ok=True)
    os.makedirs(j("06-全景沙盘"), exist_ok=True)
    with open(j("01-乐团世界书", "乐团世界书_World_Info.md"), "w", encoding="utf-8") as f:
        f.write(WORLD_INFO)
    with open(j("02-主世界编年史", "主世界事件记录与编年史_2026年10月卷.md"), "w", encoding="utf-8") as f:
        f.write(CHRONICLE)
    with open(j("03-乐团共享日记", "乐团共享日记本.md"), "w", encoding="utf-8") as f:
        f.write(DIARY)
    with open(j("04-成员记忆档案", "丰川祥子_长期记忆档案.md"), "w", encoding="utf-8") as f:
        f.write(archive)
    with open(j("05-创作者随想", "创作者世界观随想笔记.md"), "w", encoding="utf-8") as f:
        f.write("# 创作者随想\n秘密内容\n")
    with open(j("06-全景沙盘", "未来演进计划与版本发布日志.md"), "w", encoding="utf-8") as f:
        f.write("# 路线图\n机密内容\n")


def run_export(root, archive=ARCHIVE_WHITE):
    make_content(root, archive)
    chars, order, by_id, alias = er.build_registry(root)
    records, chron = er.collect_records(root, chars, order, by_id, alias, NOW)
    state = er.build_current_state(chars, order, by_id, root, records, chron, NOW, "testrev", 5)
    return records, chron, state


class GateUnlockTest(unittest.TestCase):
    def test_no_gate_returns_none(self):
        self.assertIsNone(er.gate_unlock("普通正文，无包装"))

    def test_missing_data_unlock_fails_closed(self):
        self.assertEqual(er.gate_unlock('<div class="time-gate">\n\n正文\n\n</div>'), "INVALID")

    def test_malformed_data_unlock_fails_closed(self):
        self.assertEqual(
            er.gate_unlock('<div class="time-gate" data-unlock="garbage">\n\n正文\n\n</div>'),
            "INVALID")

    def test_timezoneless_data_unlock_fails_closed(self):
        self.assertEqual(
            er.gate_unlock('<div class="time-gate" data-unlock="2026-10-06T00:00:00">\n\n正文\n\n</div>'),
            "INVALID")

    def test_valid_aware_unlock(self):
        dt = er.gate_unlock('<div class="time-gate" data-unlock="2027-01-01T00:00:00+09:00">x</div>')
        self.assertEqual(dt, datetime(2027, 1, 1, 0, 0, tzinfo=JST))


class PersonaTest(unittest.TestCase):
    def test_white(self):
        self.assertEqual(er.parse_persona(ARCHIVE_WHITE), "white")

    def test_black(self):
        self.assertEqual(
            er.parse_persona("## 5\\. 当前形态状态机\n\n当前激活状态: 黑祥 (Mortis 前奏)"), "black")

    def test_missing_section_omitted(self):
        self.assertIsNone(er.parse_persona("# 档案\n## 1\\. 记忆架构\n正文"))

    def test_ambiguous_omitted_never_synthesized(self):
        self.assertIsNone(
            er.parse_persona("## 5\\. 当前形态状态机\n\n当前激活状态: 状态平稳运行中"))


class CollectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = os.path.join(cls._tmp.name, "content")
        cls.records, cls.chron, cls.state = run_export(root)
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def recs(self, rtype):
        return [r for r in self.records if r["type"] == rtype]

    def test_unlocked_accepted_and_future_excluded(self):
        titles = " ".join(r["title"] for r in self.recs("chronicle_slice"))
        self.assertIn("已解锁切片", titles)
        self.assertIn("门控已解", titles)
        self.assertNotIn("未来切片", titles)
        self.assertNotIn("SECRET-FUTURE-BODY", json.dumps(self.records, ensure_ascii=False))

    def test_all_gate_variants_fail_closed(self):
        titles = " ".join(r["title"] for r in self.records)
        for bad in ("门控未解", "门控缺属性", "门控坏时间", "门控无时区"):
            self.assertNotIn(bad, titles)

    def test_malformed_timestamp_never_enters_runtime_nor_anchor(self):
        titles = " ".join(r["title"] for r in self.records)
        self.assertNotIn("病理切片", titles)
        self.assertNotIn("病理切片", json.dumps(self.state, ensure_ascii=False))
        # 时间不可解析的切片绝不能顶替锚点（不得 dt→now）
        self.assertIn("门控已解", self.state["world"]["title"])
        self.assertEqual(self.state["world"]["time"], "2026-10-03T10:00:00+09:00")

    def test_persona_state_explicit_values(self):
        by_id = {c["id"]: c["persona_state"] for c in self.state["characters"]}
        self.assertEqual(by_id["toyokawa_sakiko"], "white")
        self.assertIsNone(by_id["takamatsu_tomori"])  # 非 active → null
        # 歧义档案 → null，绝不合成 white
        with tempfile.TemporaryDirectory() as t:
            _, _, st = run_export(os.path.join(t, "c"),
                                  archive="## 5\\. 当前形态状态机\n\n当前激活状态: 状态平稳运行中\n")
            self.assertIsNone(next(c["persona_state"] for c in st["characters"]
                                   if c["id"] == "toyokawa_sakiko"))

    def test_partition_exclusion_05_06(self):
        blob = json.dumps(self.records, ensure_ascii=False)
        self.assertNotIn("创作者随想", blob)
        self.assertNotIn("未来演进计划", blob)
        self.assertEqual({r["type"] for r in self.records},
                         {"world_info", "chronicle_slice", "diary_entry", "memory_entry"})

    def test_world_info_meta_rules_filtered(self):
        # ADDENDUM_02 白名单：仅 1/3/4/5 号 grounded 小节放行
        wi = " ".join(r["title"] + r["text"] for r in self.recs("world_info"))
        self.assertIn("世界基准正文", wi)
        self.assertIn("地理拓扑正文", wi)
        for banned in ("置顶全局核心准则速查", "核心公共载体与资产协议",
                       "记忆持久化机制", "世界书更新与维护公约"):
            self.assertNotIn(banned, wi)
        # 运维配额规则不得以任何形式进入 runtime（含其他小节/记录引用）
        self.assertFalse(any("每人每天" in r["text"] for r in self.records))
        # 世界内概念由 03 日记记录本体承载，§2 整体排除后 diary_entry 仍在
        self.assertTrue(self.recs("diary_entry"))

    def test_content_authority_verbatim_record(self):
        # 语义冻结：内容权威逐字文本必须原样导出，无改写、无截断
        vr = [r for r in self.recs("world_info") if r["title"] == "乐团共享日记本 (The Shared Diary)"]
        self.assertEqual(len(vr), 1)
        self.assertTrue(vr[0]["text"].startswith("在少女们之间流转着一本封面素朴的硬皮记事簿"))
        self.assertTrue(vr[0]["text"].endswith("心声流淌的私密记事本。"))
        self.assertEqual(vr[0]["subject_ids"], [])
        # ID 由固定键派生，跨 snapshot 确定性
        import hashlib as _h
        self.assertEqual(vr[0]["id"], _h.sha256(
            "world_info\x00verbatim/乐团共享日记本 (The Shared Diary)".encode("utf-8")
        ).hexdigest()[:16])
        # fixture 白名单命中小节（§1、§3）2 个 + 逐字文本 1 条 = world_info 记录数
        self.assertEqual(len(self.recs("world_info")), 3)

    def test_snapshot_local_id_uniqueness(self):
        ids = [r["id"] for r in self.records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_validate_rejects_duplicate_ids(self):
        dup = self.records + [dict(self.records[0])]
        with self.assertRaises(AssertionError):
            er.validate(dup, self.state, [c["id"] for c in self.state["characters"]], NOW)

    def test_validate_accepts_clean_records(self):
        er.validate(self.records, self.state, [c["id"] for c in self.state["characters"]], NOW)


class AddendumSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.records, cls.chron, cls.state = run_export(os.path.join(cls._tmp.name, "content"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_weather_from_anchor_slice(self):
        self.assertEqual(self.state["world"]["weather"], "秋阳清澄，气温回升至19℃")

    def test_weather_missing_is_null(self):
        # 删除锚点切片的气象行后，world.weather 必须为 null（而非省略 world 或臆造）
        with tempfile.TemporaryDirectory() as t:
            root = os.path.join(t, "content")
            make_content(root)
            p = os.path.join(root, "02-主世界编年史", "主世界事件记录与编年史_2026年10月卷.md")
            with open(p, encoding="utf-8") as f:
                text = f.read().replace("* **气象**: 秋阳清澄，气温回升至19℃\n", "")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            chars, order, by_id, alias = er.build_registry(root)
            records, chron = er.collect_records(root, chars, order, by_id, alias, NOW)
            st = er.build_current_state(chars, order, by_id, root, records, chron, NOW, "r", 5)
            self.assertIsNone(st["world"]["weather"])

    def test_world_title_is_full_bracket_label(self):
        self.assertEqual(self.state["world"]["title"],
                         "2026-10-03 10:00 JST | 门控已解")

    def test_summary_prefers_gaiyou_line_without_truncation(self):
        ev = self.state["recent_events"][0]
        self.assertIn("门控已解", ev["title"])
        self.assertIn("必须完整保留于 recent_events 摘要之中", ev["summary"])
        self.assertNotIn("…", ev["summary"])
        self.assertGreater(len(ev["summary"]), 200)

    def test_summary_fallback_title_at_scene(self):
        ev = self.state["recent_events"][1]
        self.assertEqual(ev["title"], "2026-10-01 10:00 JST | 已解锁切片")
        self.assertEqual(ev["summary"],
                         "2026-10-01 10:00 JST | 已解锁切片 @ Livehouse RiNG 地下排练室")


class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.records, _, cls.state = run_export(os.path.join(cls._tmp.name, "content"))
        cls.payloads = er.build_manifest(cls.records, cls.state, NOW, "rev-test")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_manifest_sha256_and_bytes(self):
        m = json.loads(self.payloads["manifest.json"])
        for name in ("knowledge.jsonl", "current_state.json"):
            raw = self.payloads[name]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), m["files"][name]["sha256"])
            self.assertEqual(len(raw), m["files"][name]["bytes"])
        self.assertEqual(m["records"], len(self.records))
        self.assertEqual(m["schema_version"], "sakiko-starfield-runtime/v1")

    def test_knowledge_lines_all_parse(self):
        for line in self.payloads["knowledge.jsonl"].decode("utf-8").splitlines():
            self.assertIn("id", json.loads(line))


if __name__ == "__main__":
    unittest.main(verbosity=2)
