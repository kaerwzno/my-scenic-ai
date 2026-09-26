#!/usr/bin/env python3
"""把景区资料包（docx）转成结构化知识块，并**打上保护标记**。

为什么要有这一步
----------------
资料包是给人看的表格；知识库需要的是"一个景点 × 一种信息类型 = 一条可检索的事实"。
同时，**保护标记必须在这一步就打上**——等入库之后再补就晚了（见 docs/decisions.md 的 D6）。

输入：示范景区公开资料包/*.docx
输出：plugins/kb-lingshan/corpus/knowledge.json

用法：
    python build_corpus.py
    python build_corpus.py --check      # 只做保护标记的统计，不写文件
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from html import unescape
from pathlib import Path


def setup_console() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


setup_console()

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SOURCE_DIR = PROJECT / "示范景区公开资料包"
OUT_PATH = PROJECT / "plugins" / "kb-lingshan" / "corpus" / "knowledge.json"


# ── 字段 → 信息类型 ──
#
# 两个约束：
#   1. 必须和检索层的 INFO_TYPE_HINTS 对得上（用 `in` 匹配，见 vector_retriever.py）
#      location→("位置","功能")  scale→("参数","规模")  time→("开放","时间","演艺","场次")
#      highlights→("亮点","看点","注意","须知","游玩")  ticket→("门票","票价","费用")
#   2. ★ 每个字段必须映射到**互不相同**的 info_type
#      因为事实冲突门控的 key 是 (景点, info_type)，撞车会导致同景点的信息互相覆盖、丢数据
#      （这个坑实测踩过：原先"具体位置"和"核心功能"都映射成"位置与功能"，两条互相覆盖）
FIELD_TO_INFO_TYPE = {
    "具体位置": "位置",
    "核心功能": "功能",
    "建筑/景观参数": "参数与规模",
    "文化内涵": "文化内涵",
    "详细介绍": "详细介绍",
    "游玩亮点": "游玩亮点",
    "演艺/开放信息": "开放与演艺",
    "备注": "注意事项",
}

# ── 保护标记：按关键词初判（人工可改）──
#
# 判定原则：**宁可多标，不可漏标**。
#   误标 → 代价是这条知识不能自动降级（无害，人工可以改回来）
#   漏标 → 代价是安全知识可能被降级（有害，而且事后不知道是哪几条）
#
# 但"多标"不等于"乱标"：单纯出现关键词不算，要能看出**这是在给读者提示**。
#   反例：「路面采用【防滑】材料铺设」 ← 产品描述，不该保护
#   正例：「雨天湿滑，【需注意】安全」   ← 安全提示，该保护
# 所以 safety / conduct 要求"关键词 + 提示性动词"共现。
#
# 顺序即优先级：safety > service > conduct
#   （人身安全最重，服务保障次之，行为规范最轻）
PROTECT_RULES = (
    (
        "safety",
        # 触发词
        ("安全", "危险", "紧急", "疏散", "消防", "湿滑", "防滑", "滑倒",
         "跌落", "水深", "雷电", "拥挤", "陡", "台阶多"),
        # 必须共现的"提示性动词"
        ("需注意", "注意安全", "小心", "谨防", "避免", "务必", "建议提前", "切勿"),
    ),
    (
        "service",
        ("无障碍", "轮椅", "行动不便", "医疗", "救护", "医务", "急救", "AED",
         "失物", "投诉", "母婴", "寄存", "求助", "服务台", "便民"),
        (),
    ),
    (
        "conduct",
        ("禁止", "严禁", "请勿", "不得", "不可"),
        ("喧哗", "触摸", "攀爬", "拍照", "闪光灯", "吸烟", "踩踏", "采摘",
         "游泳", "垂钓", "投喂", "追逐", "打闹", "携带", "乱扔", "戏水"),
    ),
)


def detect_protection(text: str) -> tuple[bool, str]:
    """返回 (是否保护, 原因)。按 safety > service > conduct 的顺序取第一个命中。"""
    for reason, triggers, prompts in PROTECT_RULES:
        if not any(kw in text for kw in triggers):
            continue
        if prompts and not any(kw in text for kw in prompts):
            continue
        return True, reason
    return False, ""


def clean(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _cell_text(cell_xml: str) -> str:
    parts = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", cell_xml, flags=re.DOTALL)
    return clean("".join(parts))


def read_docx(path: Path) -> tuple[list[str], list[list[list[str]]]]:
    """返回 (段落, 表格)。表格：tables[表][行][列]"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")

    # 需要"文档流"：段落和表格按出现顺序排，才能知道每张表前面是哪个标题
    flow: list[tuple[str, object]] = []
    for m in re.finditer(r"(<w:p[ >].*?</w:p>)|(<w:tbl>.*?</w:tbl>)", xml, flags=re.DOTALL):
        if m.group(1):
            texts = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", m.group(1), flags=re.DOTALL)
            line = clean("".join(texts))
            if line:
                flow.append(("p", line))
        else:
            rows: list[list[str]] = []
            for tr in re.findall(r"<w:tr[ >].*?</w:tr>", m.group(2), flags=re.DOTALL):
                cells = re.findall(r"<w:tc>.*?</w:tc>", tr, flags=re.DOTALL)
                rows.append([_cell_text(c) for c in cells])
            if rows:
                flow.append(("t", rows))
    read_docx.flow = flow  # type: ignore[attr-defined]

    # ★ 关键：paragraphs / tables 都**从 flow 派生**，而不是再解析一遍 XML。
    #   否则 flow 里的表格对象和 tables 里的不是同一个对象，`payload is table` 永远不成立
    #   （这个坑实测踩过：标题提取静默失败，5 条专题知识的 spot_name 全退化成"灵山胜境"，
    #     导致 (subject, info_type) 撞车、互相覆盖）
    paragraphs = [str(p) for k, p in flow if k == "p"]
    tables = [p for k, p in flow if k == "t"]
    return paragraphs, tables


def build_spot_chunks(table: list[list[list[str]]], scenic_name: str) -> list[dict]:
    """把"景点 × 11 字段"的表转成知识块：每个景点 × 每个有内容的字段 = 一条。"""
    if not table:
        return []
    header = [clean(h) for h in table[0]]
    chunks: list[dict] = []

    for row in table[1:]:
        record = {}
        for i, col in enumerate(header):
            if i < len(row):
                record[col] = clean(row[i])
        spot_id = record.get("景点ID", "")
        spot_name = record.get("景点名称", "")
        if not spot_id or not spot_name:
            continue

        for field, info_type in FIELD_TO_INFO_TYPE.items():
            content = record.get(field, "")
            if len(content) < 4:
                continue
            protected, reason = detect_protection(content)
            chunks.append({
                "spot_id": spot_id,
                "spot_name": spot_name,
                "scenic_name": record.get("景区名称") or scenic_name,
                "info_type": info_type,
                "source_field": field,
                "content": content,
                "protected": protected,
                "protect_reason": reason,
                "priority": 100,
                "tags": [],
                "questions": [],
            })
    return chunks


def _title_before_table(flow: list, table) -> str:
    """在文档流里，找这张表之前最近的一个"像标题"的段落。

    为什么要这一步：专题表没有"景点名称"列，如果不补，spot_name 会是空的，
    导致多条知识块的 (subject, info_type) 撞车、互相覆盖（这个坑踩过两次）。
    """
    for i, (kind, payload) in enumerate(flow):
        if kind == "t" and payload is table:
            for j in range(i - 1, -1, -1):
                k2, p2 = flow[j]
                if k2 == "p":
                    text = str(p2)
                    # 太长的段落不是标题；跳过纯数字/符号
                    if 2 <= len(text) <= 20:
                        return text
            break
    return ""


def build_special_chunks(tables: list[list[list[str]]], flow: list) -> list[dict]:
    """把"指南"那本里的专题表转成知识块（大佛/梵宫/票价…）。"""
    chunks: list[dict] = []

    for idx, table in enumerate(tables, start=1):
        if not table:
            continue
        header = [clean(h) for h in table[0]]

        # 票价表：表头是 票种/价格/适用人群
        if "价格" in header and "票种" in header:
            lines = []
            for row in table[1:]:
                cells = [clean(c) for c in row]
                if len(cells) >= 2 and cells[0]:
                    lines.append("：".join(cells[:3]))
            content = "；".join(lines)
            chunks.append({
                "spot_id": "",
                "spot_name": "灵山胜境",
                "scenic_name": "灵山胜境",
                "info_type": "票价",
                "source_field": f"指南表{idx}",
                "content": content,
                "protected": False,
                "protect_reason": "",
                "priority": 100,
                "tags": ["门票", "票价", "费用"],
                # 票价是最常被问的，questions 必须写全（否则结构化快答命中不了）
                "questions": [
                    "门票多少钱？",
                    "灵山胜境门票价格是多少？",
                    "成人票多少钱一张？",
                    "老人有优惠票吗？",
                    "小孩要买票吗？",
                ],
            })
            continue

        # 普通"项目/详细信息"表：整表作为一个知识块
        if len(header) >= 2 and "项目" in header[0]:
            lines = []
            for row in table[1:]:
                cells = [clean(c) for c in row]
                if len(cells) >= 2 and cells[0]:
                    lines.append(f"{cells[0]}：{cells[1]}")
            content = "\n".join(lines)
            if len(content) < 8:
                continue
            protected, reason = detect_protection(content)
            title = _title_before_table(flow, table) or "灵山胜境"
            # 标题形如「灵山大佛：世界最高露天青铜释迦牟尼立像」，
            # 只取冒号前的部分——否则这个长串会被当成实体名，用户永远不会那样提问
            title = title.split("：", 1)[0].split(":", 1)[0].strip() or title
            chunks.append({
                "spot_id": "",
                "spot_name": title,       # ← 从文档流里取表前的标题补上
                "scenic_name": "灵山胜境",
                "info_type": "专题知识",
                "source_field": f"指南表{idx}",
                "content": content,
                "protected": protected,
                "protect_reason": reason,
                "priority": 100,
                "tags": [],
                "questions": [],
            })

    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只统计保护标记，不写文件")
    parser.add_argument("--scenic", default="", help="只输出指定景区（如「灵山胜境」「拈花湾禅意小镇」）")
    parser.add_argument("--out", default="", help="输出路径（默认 plugins/kb-lingshan/corpus/knowledge.json）")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else OUT_PATH

    if not SOURCE_DIR.is_dir():
        print(f"资料包目录不存在: {SOURCE_DIR}")
        return 2

    structured = None
    guideline = None
    for f in sorted(SOURCE_DIR.glob("*.docx")):
        if "结构化数据集" in f.name:
            structured = f
        elif "指南" in f.name:
            guideline = f

    chunks: list[dict] = []

    if structured:
        _, tables = read_docx(structured)
        for t in tables:
            chunks.extend(build_spot_chunks(t, "灵山胜境"))

    if guideline:
        paras, tables = read_docx(guideline)
        flow = getattr(read_docx, "flow", [])
        chunks.extend(build_special_chunks(tables, flow))

    # 按景区过滤（为了把一个资料包拆成多个独立知识库插件）
    if args.scenic:
        before = len(chunks)
        chunks = [c for c in chunks if c["scenic_name"] == args.scenic]
        print(f"按景区过滤「{args.scenic}」：{before} → {len(chunks)} 条")

    # ★ 保留已有的增强结果（tags / questions），避免重新构建时白跑一遍 LLM
    #   匹配键用 (spot_id, info_type, source_field) —— 这三个字段由资料包结构决定，稳定
    kept = 0
    if out_path.is_file() and not args.check:
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            old_index = {
                (c.get("spot_id"), c.get("info_type"), c.get("source_field")): c
                for c in old.get("chunks", [])
            }
            for c in chunks:
                prev = old_index.get((c["spot_id"], c["info_type"], c["source_field"]))
                if not prev:
                    continue
                # ★ 逐字段保留，且只保留**非空**的：
                #   否则旧文件里的空数组会覆盖掉新代码里写死的内容（票价那条就中过招）
                if prev.get("tags"):
                    c["tags"] = prev["tags"]
                if prev.get("questions"):
                    c["questions"] = prev["questions"]
                if prev.get("tags") or prev.get("questions"):
                    kept += 1
        except (json.JSONDecodeError, OSError):
            pass

    # 统计
    protected = [c for c in chunks if c["protected"]]
    by_reason: dict[str, int] = {}
    for c in protected:
        by_reason[c["protect_reason"]] = by_reason.get(c["protect_reason"], 0) + 1
    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c["info_type"]] = by_type.get(c["info_type"], 0) + 1

    print("=" * 70)
    print(f"知识块总数：{len(chunks)}")
    print(f"景点数    ：{len({c['spot_id'] for c in chunks if c['spot_id']})}")
    print(f"按信息类型：{json.dumps(by_type, ensure_ascii=False)}")
    print(f"★ 受保护  ：{len(protected)} 条  {json.dumps(by_reason, ensure_ascii=False)}")
    if kept:
        print(f"已保留增强：{kept} 条的 tags/questions（无需重跑 LLM）")
    print("=" * 70)
    if protected:
        print("受保护知识清单：")
        for c in protected:
            label = c["spot_name"] or c["source_field"]
            print(f"  [{c['protect_reason']:7}] {label:14} {c['info_type']:8} {c['content'][:52]}")

    if args.check:
        return 0

    payload = {
        "version": 1,
        "scenic": "灵山胜境",
        "source": SOURCE_DIR.name,
        "chunk_count": len(chunks),
        "protected_count": len(protected),
        "chunks": chunks,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"已写出：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
