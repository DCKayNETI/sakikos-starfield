r"""
Runtime Export V1 — STARFIELD_FLESH_CONTRACT_V1 的 Starfield 侧实现。

canonical content/（post-sync 状态）
→ deterministic parser + authority gate（复用 sync_gdrive 的时钟与切片解析常量）
→ manifest.json / current_state.json / knowledge.jsonl

语义依据（内容侧 STARFIELD_RUNTIME_CONTENT_SEMANTICS_V1）：
- 锚点切片 = MAX(slice.timestamp) WHERE timestamp <= 当前 JST；未来切片一律不导出。
- persona_state 仅取自成员档案「当前形态状态机」：白祥→white / 黑祥→black；
  小节缺失或取值不可辨 → None（省略），绝不合成默认人格。
- world.summary 禁止生成；recent_events 摘要优先取切片「概要」行，缺失时回退
  「标题 @ 场景」，禁止硬截断。
- world.weather 取锚点切片「气象」行（ADDENDUM 定义的格式字段），缺失 → null。
- world.title 取切片标头【】内完整标签文本。

grounded include-list（合同 §2.4，Starfield 私有）：01 世界书 / 02 编年史 / 03 共享日记
/ 04 成员档案。05 创作者随想、06 全景沙盘（含地图/沙盘 UI 及路线图日志）、index/导览
一律不导出——地图界面后续迭代与 runtime 内容完全解耦。

独立可执行（deploy.yml 仅是 caller 之一）：
    python3 scripts/export_runtime.py [--content-dir content] [--out-dir public/static/runtime]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_gdrive import JST, SLICE_HEADER, SLICE_TIME, LINK, GATE_BADGE, _slice_unlock  # noqa: E402

RUNTIME_SCHEMA = "sakiko-starfield-runtime/v1"
STATE_SCHEMA = "sakiko-starfield-state/v1"

# (目录, record type, 切分模式)。顺序决定 knowledge.jsonl 的输出顺序。
PARTITIONS = [
    ("01-乐团世界书", "world_info", "sections"),
    ("02-主世界编年史", "chronicle_slice", "slices"),
    ("03-乐团共享日记", "diary_entry", "slices"),
    ("04-成员记忆档案", "memory_entry", "sections"),
]

# 小节级切分：# 与 ## 为边界（### 以下并入上级小节）。
SECTION_HEAD = re.compile(r"^#{1,2} .+$", re.M)
PERSONA_HEAD = re.compile(r"^#{1,2}.*当前形态状态机.*$", re.M)
# 切片内结构化行（ADDENDUM 定义的格式字段）。兼容 "* **标签**: …" 的项目符号/粗体
# 星号前缀；行锚定避免误配正文，捕获段尾部留待 clean_text 处理
SCENE_LINE = re.compile(r"^[\s*#]*核心场景\*{0,2}\s*[:：]\s*(.+?)\s*$", re.M)
WEATHER_LINE = re.compile(r"^[\s*#]*气象\*{0,2}\s*[:：]\s*(.+?)\s*$", re.M)
SUMMARY_LINE = re.compile(r"^[\s*#]*概要\*{0,2}\s*[:：]\s*(.+?)\s*$", re.M)
# World Info 中的运维性元规则小节（ADDENDUM：NON_GROUNDED），按标题排除
NON_GROUNDED_WORLD_INFO = ("置顶全局核心准则速查",)
GATE_OPEN_ATTR = re.compile(r'<div class="time-gate"[^>]*data-unlock="([^"]+)"[^>]*>')
GATE_OPEN_ANY = re.compile(r'<div class="time-gate"[^>]*>\n?')
GATE_BADGE_DIV = re.compile(r'<div class="time-gate-badge">.*?</div>\n?')
GATE_CLOSE_LINE = re.compile(r"^</div>\s*$", re.M)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# 发布后置断言：任何 payload 文本中出现即失败（fail closed）
FORBIDDEN = (GATE_BADGE, 'class="time-gate"', "data-unlock")


def read_frontmatter(text):
    """返回 (title, aliases, body)。仅解析 title 与 aliases 列表，其余 frontmatter 忽略。"""
    if not text.startswith("---"):
        return None, [], text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, [], text
    fm, body = parts[1], parts[2].lstrip("\n")
    title, aliases, in_aliases = None, [], False
    for line in fm.splitlines():
        s = line.strip()
        if s.startswith("aliases:"):
            in_aliases = True
        elif in_aliases and s.startswith("- "):
            aliases.append(s[2:].strip().strip('"'))
        else:
            in_aliases = False
            if s.startswith("title:"):
                title = s[6:].strip().strip('"')
    return title, aliases, body


def clean_text(text):
    """确定性文本化：剥门控包装/HTML 注释，wikilink 还原为显示文本，还原导出转义。"""
    text = HTML_COMMENT.sub("", text)
    text = GATE_BADGE_DIV.sub("", text)
    text = GATE_OPEN_ANY.sub("", text)
    text = GATE_CLOSE_LINE.sub("", text)
    text = LINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    for esc, ch in (("\\.", "."), ("\\!", "!"), ("\\-", "-"), ("\\,", ","), ("\\~", "~")):
        text = text.replace(esc, ch)
    return text.strip()


def clean_title(title):
    """标题文本化：wikilink 还原为显示文本，剥 **/# 包装，还原导出转义。"""
    title = LINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), title)
    for esc, ch in (("\\.", "."), ("\\!", "!"), ("\\~", "~")):
        title = title.replace(esc, ch)
    return re.sub(r"^[#*\s]+|[*\s]+$", "", title).strip()


def split_blocks(body, mode):
    """切分为 (标题, 正文块) 列表。sections：# / ## 边界；slices：【…】切片边界，
    首个边界之前的 preamble（卷首宪章/使用协议等编辑性文本）不导出。"""
    if mode == "slices":
        headers = list(SLICE_HEADER.finditer(body))
        get_title = lambda m: clean_title(re.sub(r"\*+", "", m.group("label")))
    else:
        headers = list(SECTION_HEAD.finditer(body))
        get_title = lambda m: clean_title(m.group(0))
    blocks = []
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        blocks.append((get_title(h), body[h.end():end]))
    return blocks


def gate_unlock(body):
    """门控状态判定，fail closed：
    - 无 gate → None（视为无门控）
    - 有 gate 但缺 data-unlock / 时间不可解析 / 无时区 → "INVALID"（整条剔除）
    - 合法 offset-aware datetime → 返回该时刻（按当前 JST 重判）
    """
    if not GATE_OPEN_ANY.search(body):
        return None
    m = GATE_OPEN_ATTR.search(body)
    if not m:
        return "INVALID"
    try:
        dt = datetime.fromisoformat(m.group(1))
    except ValueError:
        return "INVALID"
    if dt.tzinfo is None:
        return "INVALID"
    return dt


def build_registry(content_dir):
    """加载私有注册表，构建 别名→id 映射（stem/短名/frontmatter aliases）。"""
    reg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_registry.json")
    with open(reg_path, encoding="utf-8") as f:
        chars = json.load(f)["characters"]
    order = [c["id"] for c in chars]
    by_id = {c["id"]: c for c in chars}
    alias = {}
    for c in chars:
        alias[c["archive"][:-3]] = c["id"]
        alias[c["display_name"]] = c["id"]
        fm_path = os.path.join(content_dir, "04-成员记忆档案", c["archive"])
        if os.path.exists(fm_path):
            with open(fm_path, encoding="utf-8") as f:
                _, aliases, _ = read_frontmatter(f.read())
            for a in aliases:
                alias.setdefault(a, c["id"])
    return chars, order, by_id, alias


def subjects_from_links(raw, alias, order):
    ids = set()
    for target, al in LINK.findall(raw):
        for key in (target.strip(), (al or "").strip()):
            if key in alias:
                ids.add(alias[key])
    return [i for i in order if i in ids]


def parse_persona(body):
    """当前形态状态机 → white/black（ADDENDUM：active 仅祥子，v1 值域 white/black）。
    canonical 小节缺失或取值不可辨 → None（字段输出 null），绝不合成默认人格。"""
    m = PERSONA_HEAD.search(body)
    if not m:
        return None
    nxt = SECTION_HEAD.search(body, m.end())
    seg = body[m.end(): nxt.start() if nxt else len(body)]
    a = re.search(r"当前激活状态[^:：]*[:：]\s*(.+)", seg)
    if a:
        val = a.group(1)
        if "黑祥" in val:
            return "black"
        if "白祥" in val:
            return "white"
    return None


def collect_records(content_dir, chars, order, by_id, alias, now):
    records, chron = [], []
    seq = 0
    for dirname, rtype, mode in PARTITIONS:
        d = os.path.join(content_dir, dirname)
        if not os.path.isdir(d):
            continue
        archive_ids = {c["archive"][:-3]: c["id"] for c in chars}
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            _, _, body = read_frontmatter(raw)
            stem = fn[:-3]
            for title, block in split_blocks(body, mode):
                seq += 1
                dt = _slice_unlock(title) if mode == "slices" else None
                if mode == "slices":
                    if dt is None or dt > now:
                        continue  # 切片时间不可解析或属于未来 → fail closed，不入 grounded runtime
                    g = gate_unlock(block)
                    if g == "INVALID" or (g is not None and g > now):
                        continue  # 门控失效或仍未解锁 → 整条剔除
                if rtype == "world_info" and any(k in title for k in NON_GROUNDED_WORLD_INFO):
                    continue  # ADDENDUM：运维性元规则 NON_GROUNDED
                text = clean_text(block)
                if not re.sub(r"[-|:\s\\]", "", text):
                    continue  # 仅含分隔线/表格骨架等排版残渣的小节不导出
                rec = {
                    "id": hashlib.sha256(
                        f"{rtype}\x00{dirname}/{fn}\x00{seq}".encode("utf-8")).hexdigest()[:16],
                    "type": rtype,
                    "subject_ids": [],
                    "title": title,
                    "text": text,
                }
                if rtype == "world_info":
                    pass  # 世界书为全局内容，无主体归属
                elif rtype == "chronicle_slice":
                    rec["subject_ids"] = subjects_from_links(title + block, alias, order)
                elif rtype == "diary_entry":
                    rec["subject_ids"] = subjects_from_links(title + block, alias, order)
                elif rtype == "memory_entry":
                    rec["subject_ids"] = [archive_ids[stem]] if stem in archive_ids else []
                if dt:
                    rec["world_date"] = dt.date().isoformat()
                    rec["unlock_at"] = dt.isoformat()
                records.append(rec)
                if rtype == "chronicle_slice":
                    sm = SCENE_LINE.search(block)
                    wm = WEATHER_LINE.search(block)
                    gm = SUMMARY_LINE.search(block)
                    if gm:
                        summary = clean_text(gm.group(1))  # ADDENDUM：概要行优先，禁止截断
                    else:
                        scene_text = clean_text(sm.group(1)) if sm else None
                        summary = rec["title"] + (" @ " + scene_text if scene_text else "")
                    chron.append((dt, seq, rec,
                                  clean_text(sm.group(1)) if sm else None,
                                  clean_text(wm.group(1)) if wm else None,
                                  summary))
    return records, chron


def build_current_state(chars, order, by_id, content_dir, records, chron, now, revision, recent_n):
    state = {
        "schema_version": STATE_SCHEMA,
        "world_time": now.replace(microsecond=0).isoformat(),
        "world_revision": revision,
        "characters": [],
    }
    for c in chars:
        entry = {"id": c["id"], "display_name": c["display_name"]}
        p = os.path.join(content_dir, "04-成员记忆档案", c["archive"])
        persona = None
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                _, _, body = read_frontmatter(f.read())
            persona = parse_persona(body)
        entry["persona_state"] = persona  # ADDENDUM：active 仅祥子，其余 null；不可辨 → null
        state["characters"].append(entry)
    if chron:
        chron.sort(key=lambda x: (x[0], x[1]))
        anchor_dt, _, anchor, anchor_scene, anchor_weather, _ = chron[-1]
        world = {
            "time": anchor_dt.isoformat(),
            "title": anchor["title"],  # ADDENDUM：切片标头【】内完整标签文本
            "weather": anchor_weather,  # ADDENDUM：气象行缺失 → null
        }
        if anchor_scene:
            world["scene"] = anchor_scene
        state["world"] = world
        state["recent_events"] = [
            {"time": dt.isoformat(), "title": rec["title"], "summary": summary}
            for dt, _, rec, _, _, summary in chron[-recent_n:][::-1]
        ]
    # world.summary：内容侧禁止生成，永不输出。
    return state


def world_revision(content_dir):
    """opaque revision（合同 §2.6）：git 短 sha；content/ 有未提交改动时加 -dirty；
    无 git 环境时退化为内容树哈希。策略为 Starfield 内部事务。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def git(*args):
        return subprocess.run(["git", "-C", root, *args], capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    try:
        sha = git("rev-parse", "--short=10", "HEAD")
        if sha:
            dirty = git("status", "--porcelain", "--", content_dir)
            return sha + ("-dirty" if dirty else "")
    except Exception:
        pass
    h = hashlib.sha256()
    for dp, _, fns in os.walk(content_dir):
        for fn in sorted(fns):
            if fn.endswith(".md"):
                fp = os.path.join(dp, fn)
                h.update(os.path.relpath(fp, content_dir).replace("\\", "/").encode("utf-8"))
                with open(fp, "rb") as f:
                    h.update(f.read())
    return "local-" + h.hexdigest()[:12]


def validate(records, state, order, now):
    """发布前自校验（合同 §2.5：完整校验后才发布）。任何失败直接终止，不产出半成品。"""
    assert records, "knowledge 为空：解析器与内容结构可能已脱节"
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)), "knowledge ID 在 snapshot 内重复"
    valid = set(order)
    for r in records:
        assert not any(f in r["title"] or f in r["text"] for f in FORBIDDEN), \
            f"门控残留泄漏：{r['id']} {r['title'][:40]}"
        assert set(r["subject_ids"]) <= valid, f"未知 subject_id：{r['id']}"
        if r["type"] in ("chronicle_slice", "diary_entry"):
            assert "unlock_at" not in r or datetime.fromisoformat(r["unlock_at"]) <= now, \
                f"未来切片逃过 authority gate：{r['title'][:40]}"
    blob = json.dumps(state, ensure_ascii=False)
    assert not any(f in blob for f in FORBIDDEN), "current_state 含门控残留"


def build_manifest(records, state, now, revision):
    """组装三 payload（合同 §2.2：manifest 携带 payload sha256 + bytes）。"""
    knowledge = "".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records
    ).encode("utf-8")
    state_bytes = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest = {
        "schema_version": RUNTIME_SCHEMA,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "world_revision": revision,
        "files": {
            "knowledge.jsonl": {"sha256": hashlib.sha256(knowledge).hexdigest(),
                                "bytes": len(knowledge)},
            "current_state.json": {"sha256": hashlib.sha256(state_bytes).hexdigest(),
                                   "bytes": len(state_bytes)},
        },
        "records": len(records),
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return {"knowledge.jsonl": knowledge,
            "current_state.json": state_bytes,
            "manifest.json": manifest_bytes}


def publish(out_dir, payloads):
    """暂存 → 校验字节一致 → payload 先落位、manifest 最后（入口文件最后可见）。"""
    os.makedirs(out_dir, exist_ok=True)
    tmp = {}
    for name, data in payloads.items():
        p = os.path.join(out_dir, "." + name + ".tmp")
        with open(p, "wb") as f:
            f.write(data)
        tmp[name] = p
    for name, data in payloads.items():
        with open(tmp[name], "rb") as f:
            assert f.read() == data, f"暂存校验失败：{name}"
    for name in ("knowledge.jsonl", "current_state.json", "manifest.json"):
        os.replace(tmp[name], os.path.join(out_dir, name))


def main():
    ap = argparse.ArgumentParser(description="Export STARFIELD_FLESH_CONTRACT_V1 runtime snapshot")
    ap.add_argument("--content-dir", default="content")
    ap.add_argument("--out-dir", default="public/static/runtime")
    ap.add_argument("--recent-events", type=int, default=5)
    args = ap.parse_args()

    now = datetime.now(JST)
    chars, order, by_id, alias = build_registry(args.content_dir)
    records, chron = collect_records(args.content_dir, chars, order, by_id, alias, now)
    revision = world_revision(args.content_dir)
    state = build_current_state(chars, order, by_id, args.content_dir, records, chron,
                                now, revision, args.recent_events)
    validate(records, state, order, now)

    payloads = build_manifest(records, state, now, revision)
    publish(args.out_dir, payloads)

    by_type = {}
    for r in records:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    print(f"[runtime] revision={revision} records={len(records)} {by_type}")
    if chron:
        print(f"[runtime] anchor={chron[-1][2]['title'][:48]}")
    personas = {c['id']: c.get('persona_state') for c in state['characters']
                if c.get('persona_state')}
    print(f"[runtime] persona_state={personas}")
    print(f"[runtime] snapshot -> {args.out_dir}")


if __name__ == "__main__":
    main()
