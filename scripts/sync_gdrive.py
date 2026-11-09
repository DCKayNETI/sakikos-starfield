r"""
Google Drive to Quartz content/ Sync Helper
Includes all 15 core documents: World Info, Chronicle, Diary, Creator Notes, and 11 Member Memory Archives.

Google Drive 云端文档为唯一权威源（Single Source of Truth）；本脚本将其 Markdown 导出
拉取覆盖到仓库 content/。导出结果会对方括号做逐字符转义（\[\[...\]\]），
Quartz 的双链解析器无法识别，因此先经 unescape_wikilinks() 管道还原。
"""
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

DOCS_MAP = {
    # 核心载体
    "1TpwLPKhmtwAGInf2WfUYyFoQsL3QO4gmVIO4zFOfGYo": "content/01-乐团世界书/乐团世界书_World_Info.md",
    "1uMkTfLos3ia8QAOoKPY5ixFA7eSs87LmBhpR0idFKbA": "content/02-主世界编年史/主世界事件记录与编年史_2026年10月卷.md",
    "1k3RmSwz9G8zCDk97fut72KAfh3odRNt3SQ9fxlQglik": "content/03-乐团共享日记/乐团共享日记本.md",
    "1QixMpZwdImV1a65hx8jr5j_mWWPAIOeJv9FSxlp4TgY": "content/05-创作者随想/创作者世界观随想笔记.md",

    "1bMIVQ31xip0fegW6EsIheaRxZ29qjmDSJyofBU6KKw8": "content/06-全景沙盘/未来演进计划与版本发布日志.md",

    # 11 份记忆档案已移出映射：权威源为仓库（demux 独占写入），云端 Docs 封存为历史快照
}

# Google Docs 的 Markdown 导出会把字面 [[...]] 逐括号转义成 \[\[...\]\]，
# 且括号内的字符（如 MyGO!!!!! 的 !）也会被转义成 \!，Quartz 的双链解析器
# 无法识别，必须整段还原。未参与双链的孤立转义括号保持原样，不影响渲染。
ESCAPED_WIKILINK = re.compile(r"\\\[\\\[([^\n]+?)\\\]\\\]")


# 公约示例占位符：云端文档里作为示例书写的字面 [[...]] 经转义还原后会
# 变成真实的双链，在图谱中生成幽灵节点，这里做定点中性化。
GHOST_EXAMPLES = {
    "[[角色名]]": "『角色名』",
    "[[地标]]": "『地标』",
}


def unescape_wikilinks(text: str) -> str:
    """把导出中的 \\[\\[...\\]\\] 还原为字面 [[...]]，并去掉链接目标内的转义符。"""
    return ESCAPED_WIKILINK.sub(lambda m: "[[" + m.group(1).replace("\\", "") + "]]", text)


def sync_doc(doc_id: str, target_path: str, attempts: int = 3, timeout: int = 120) -> bool:
    """拉取单个 doc。Google 导出偶发 read timeout（大文档/并发编辑时更明显），
    单次失败不代表文档不可达：同一次运行内最多 attempts 次（退避 5s/10s），
    全部失败才判该文档失败（整体 run 仍 fail closed，不提交半套内容）。"""
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=md"
    last_err = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(5 * (attempt - 1))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                content = resp.read().decode("utf-8-sig", errors="replace").replace("\ufeff", "")
            content = unescape_wikilinks(content)
            for ghost, neutral in GHOST_EXAMPLES.items():
                content = content.replace(ghost, neutral)

            # frontmatter（title/tags/aliases 等）只在仓库侧维护，同步时原样保留
            frontmatter = ""
            if os.path.exists(target_path):
                with open(target_path, "r", encoding="utf-8") as f:
                    old_text = f.read()
                if old_text.startswith("---"):
                    parts = old_text.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = f"---{parts[1]}---\n\n"

            final_content = frontmatter + content
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(final_content)
            print(f"[OK] Synced: {target_path}")
            return True
        except Exception as e:
            last_err = e
            print(f"[retry] {target_path} attempt {attempt}/{attempts} failed: {e}", file=sys.stderr)
    print(f"[FAIL] {target_path} (doc {doc_id}): {last_err}", file=sys.stderr)
    return False



# ─────────────────────────────────────────────────────────────
# 编年史自动分流（demux）：从编年史卷提取「个人记忆分流指引」，
# 将主观切片增量追加到对应成员记忆档案的「4. 主世界历史与日常演进纪要」。
# 追加式账本：已分流切片不重复处理，不支持追溯修改（会告警）。
# ─────────────────────────────────────────────────────────────
# 编年史卷索引文档：内容为「YYYY-MM: <Drive文档ID>」行，管道先读索引、
# 再按索引动态发现并同步所有卷。该 ID 配置后，跨月由祥子在索引文档中
# 自行追加一行即可，无需任何仓库侧操作。
VOLUME_INDEX_DOC_ID = "1g_EZcSdyiZeRY_BAkJYnESBTy8E1IThea89NPqc_l7A"


def load_volume_index() -> dict:
    """读取卷索引文档，返回 {月份: doc_id}；索引未配置或不可达时返回空。"""
    if not VOLUME_INDEX_DOC_ID:
        return {}
    try:
        url = f"https://docs.google.com/document/d/{VOLUME_INDEX_DOC_ID}/export?format=txt"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
    except Exception as e:
        print(f"[volumes] 卷索引读取失败（沿用既有卷映射）: {e}", file=sys.stderr)
        return {}
    volumes = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\d{4}-\d{2})\s*[:：]\s*([A-Za-z0-9_-]{20,})", line)
        if m:
            volumes[m.group(1)] = m.group(2)
    print(f"[volumes] 索引发现 {len(volumes)} 卷: {sorted(volumes)}")
    return volumes


MEMORIES_DIR = os.path.join("content", "04-成员记忆档案")
DEMUX_SECTION = "## 4. 主世界历史与日常演进纪要"
SLICE_HEADER = re.compile(r"^#{2,4}\s*\*{0,2}【(?P<label>[^】]+)】\*{0,2}\s*$", re.M)
DEMUX_ANCHOR = re.compile(r"^[*\s]*个人记忆分流指引\*{0,2}\s*[::]\s*$", re.M)
LINK = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")
SLICE_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?")
GATE_BADGE = "🔒 星轨观测锁定"


def _slice_unlock(label: str):
    """从切片标签解析 JST 解锁时刻；无时分按当日 00:00 JST。无法解析返回 None。"""
    m = SLICE_TIME.search(label)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2) or '00:00'}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=JST)


def _clean(text: str) -> str:
    text = text.replace("\\-", "-").replace("\\!", "!").replace("\\.", ".").replace("\\,", ",")
    text = text.replace("**", "").replace("`", "").strip()
    return re.sub(r"\s+", " ", text)


def _memory_file_index():
    """成员名 → 档案文件路径 的映射（同时登记全名与短名）。"""
    index = {}
    if os.path.isdir(MEMORIES_DIR):
        for fn in os.listdir(MEMORIES_DIR):
            if fn.endswith(".md"):
                stem = fn[:-3]
                index[stem] = os.path.join(MEMORIES_DIR, fn)
                index[stem.split("_")[0]] = os.path.join(MEMORIES_DIR, fn)
    return index


def apply_time_gates(volume_path: str, warnings: list) -> int:
    """为时间戳 > 当前 JST 的切片正文加毛玻璃门控包装（标头保持可见）。
    客户端 JS 按 data-unlock 在解锁时刻平滑淡出；文件级幂等：每次同步基于
    Drive 新内容重算。返回门控切片数。"""
    with open(volume_path, "r", encoding="utf-8") as f:
        text = f.read()
    # 剥除上一轮残留的门控包装（包装行会残留在切片正文中，不清除会永久累积）
    text = re.sub(r'<div class="time-gate"[^>]*>\n\n?', '', text)
    text = re.sub(r'<div class="time-gate-badge">.*?</div>\n\n?', '', text)
    text = re.sub(r'^</div>\n\n?', '', text, flags=re.M)
    headers = list(SLICE_HEADER.finditer(text))
    if not headers:
        return 0
    now = datetime.now(JST)
    pieces = [text[: headers[0].start()]]
    gated = 0
    for i, h in enumerate(headers):
        label = h.group("label").strip()
        header_line = h.group(0)
        body_start = h.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end].strip("\n")
        unlock = _slice_unlock(label)
        if unlock and unlock > now:
            iso = unlock.strftime("%Y-%m-%dT%H:%M:00+09:00")
            teaser = unlock.strftime("%m-%d %H:%M")
            pieces.append(
                header_line + "\n\n"
                + '<div class="time-gate" data-unlock="' + iso + '">\n\n'
                + '<div class="time-gate-badge">' + GATE_BADGE
                + " · 将于 " + teaser + " JST 抵达成像点</div>\n\n"
                + body + "\n\n</div>\n\n"
            )
            gated += 1
        else:
            pieces.append(text[h.start():body_end].rstrip("\n") + "\n\n")
    with open(volume_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(pieces))
    return gated


def demux_volume(volume_path: str, name_index: dict, warnings: list) -> int:
    with open(volume_path, "r", encoding="utf-8") as f:
        text = f.read()
    headers = list(SLICE_HEADER.finditer(text))
    if not headers:
        return 0
    appended = 0
    for i, h in enumerate(headers):
        label = h.group("label").strip()
        unlock = _slice_unlock(label)
        if unlock and unlock > datetime.now(JST):
            continue  # 时间锁：未来切片延迟分流，到解锁时刻由后续轮询自然入库
        body_start = h.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end]
        anchor = DEMUX_ANCHOR.search(body)
        if not anchor:
            continue
        tail = body[anchor.end():]
        entries = []
        for raw_line in tail.splitlines():
            line = _clean(raw_line)
            if not line:
                continue
            links = LINK.findall(line)
            if not links:
                break  # 分流块结束（遇到第一个非条目行）
            desc = LINK.sub("", line)
            desc = re.sub(r"^[\-\s:*&]+", "", desc).strip(" :：-")
            for target, alias in links:
                entries.append((target.strip(), alias, desc))
        if not entries:
            warnings.append(f"[demux] 切片「{label[:40]}」的分流块无法解析，已原样保留于卷内")
            continue
        for target, alias, desc in entries:
            path = name_index.get(target) or name_index.get((alias or "").strip())
            if not path:
                warnings.append(f"[demux] 未知目标「{target}」，条目保留于卷内：{desc[:40]}")
                continue
            with open(path, "r", encoding="utf-8") as f:
                mem_text = f.read()
            if f"【{label[:18]}" in mem_text:
                continue  # 双层去重②：目标档案已含该切片标记
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                if DEMUX_SECTION not in mem_text:
                    f.write(f"\n\n# **{DEMUX_SECTION}**\n\n")
                f.write(f"- **【{label}】** {desc}\n")
            appended += 1
    return appended


def demux_chronicle_to_profiles() -> None:
    name_index = _memory_file_index()
    warnings = []
    total = 0
    chronicle_dir = os.path.join("content", "02-主世界编年史")
    for fn in sorted(os.listdir(chronicle_dir)):
        if fn.endswith(".md") and "卷" in fn:
            total += demux_volume(os.path.join(chronicle_dir, fn), name_index, warnings)
    print(f"[demux] 分流完成：新增记忆条目 {total} 条")
    for w in warnings:
        print(w, file=sys.stderr)


if __name__ == "__main__":
    volume_map = load_volume_index()
    all_docs = dict(DOCS_MAP)
    for month, doc_id in volume_map.items():
        year, mon = month.split("-")
        all_docs[doc_id] = f"content/02-主世界编年史/主世界事件记录与编年史_{year}年{mon}月卷.md"
    print(f"Starting sync of {len(all_docs)} documents from Google Drive...")
    failures = [(d, p) for d, p in all_docs.items() if not sync_doc(d, p)]
    if failures:
        print(f"\n{len(failures)}/{len(DOCS_MAP)} documents FAILED to sync:")
        for d, p in failures:
            print(f"  - {p}")
        sys.exit(1)
    print("Sync complete: all documents pulled successfully.")
    # 时间锁：为未来切片加毛玻璃门控（延迟分流的前提）
    chronicle_dir = os.path.join("content", "02-主世界编年史")
    gated_total = 0
    for fn in sorted(os.listdir(chronicle_dir)):
        if fn.endswith(".md") and "卷" in fn:
            gated_total += apply_time_gates(os.path.join(chronicle_dir, fn), [])
    print(f"[time-gate] 门控未来切片 {gated_total} 个")
    # demux：只分流已解锁切片（未来切片延迟入库）
    demux_chronicle_to_profiles()
