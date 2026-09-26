#!/usr/bin/env python3
"""把标准测试问题集渲染成一份人看的文档。

数据源只有一个：docs/testset.json
产物只有一个：docs/test-questions.md

为什么要这么做：题号、条数出现在文档的很多地方（目录、小节标题、合计）。
手写的话早晚会出现"表格里写 100 条、实际列了 98 条"这种对不上的情况，
而这份题库是要拿去做论文实验数据的，数字必须自洽。

用法：
    python render_testset.py            # 生成 docs/test-questions.md
    python render_testset.py --check    # 只校验不写文件（条数是否和声明一致）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEST_SET = ROOT / "docs" / "testset.json"
OUT_FILE = ROOT / "docs" / "test-questions.md"


def _fix_console() -> None:
    """Windows 控制台默认不是 UTF-8，中文会打成乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def load() -> dict:
    data = json.loads(TEST_SET.read_text(encoding="utf-8"))
    if not isinstance(data.get("items"), list):
        raise SystemExit("testset.json 里没有 items 列表")
    return data


def validate(data: dict) -> list[str]:
    """返回问题列表（空 = 没问题）。"""
    problems: list[str] = []
    items = data["items"]
    declared = {c["code"]: c["count"] for c in data["categories"]}
    actual = Counter(i["cat"] for i in items)

    for code, want in declared.items():
        got = actual.get(code, 0)
        if got != want:
            problems.append(f"类别 {code}：声明 {want} 条，实际 {got} 条")
    for code in actual:
        if code not in declared:
            problems.append(f"类别 {code}：有问题但没有在 categories 里声明")

    ids = [i["id"] for i in items]
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        problems.append(f"题号重复：{', '.join(sorted(dup))}")

    missing = [i["id"] for i in items if not (i.get("q") or "").strip()
               or not (i.get("expect") or "").strip()]
    if missing:
        problems.append(f"缺问题或缺期望要点：{', '.join(missing)}")

    return problems


def render(data: dict) -> str:
    items = data["items"]
    cats = data["categories"]
    by_cat: dict[str, list[dict]] = {c["code"]: [] for c in cats}
    for it in items:
        by_cat.setdefault(it["cat"], []).append(it)

    out: list[str] = []
    w = out.append

    w("# 标准测试问题集")
    w("")
    w(f"版本 {data.get('_版本', '')} ｜ 共 **{len(items)}** 条")
    w("")
    w("这份文档回答三个问题：**问了什么、每类问题在测什么、拿什么标准判对错**。"
      "答案记录在配套的 `test-results.md` 里，逐条对应。")
    w("")
    w("题目的唯一数据源是 `testset.json`，本文档由 `scripts/render_testset.py` 生成。"
      "要改题、加题，只改 JSON，然后重新生成——这样文档里的条数不会和实际对不上。")
    w("")

    # ── 一、类别与条数 ──
    w("---")
    w("")
    w("## 一、类别、条数，以及每类在测什么")
    w("")
    w("| 代码 | 类别 | 条数 | 这类问题在测什么 | 判定口径 | 产出指标 |")
    w("|---|---|---|---|---|---|")
    for c in cats:
        w(f"| `{c['code']}` | **{c['name']}** | {c['count']} | {c['what']} | "
          f"{c['criteria']} | {c['metric']} |")
    w(f"|  | **合计** | **{len(items)}** |  |  |  |")
    w("")
    w("### 每一类为什么值得单独测")
    w("")
    for c in cats:
        w(f"**{c['name']}（{c['code']}，{c['count']} 条）** —— {c['why']}")
        w("")

    # ── 二、判定口径 ──
    w("---")
    w("")
    w("## 二、怎么算对、怎么算错")
    w("")
    w("| 判定 | 含义 |")
    w("|---|---|")
    w("| ✅ 命中 | 关键数字或关系与「期望要点」一致 |")
    w("| ⚠️ 部分命中 | 方向对、但不全（比如该列 6 个景点只列了 4 个） |")
    w("| ❌ 未命中 | 说错数字、答非所问、或检索没给到 |")
    w("| 🚫 编造 | 资料里没有却给出具体信息（只在这一种情况下算严重错误） |")
    w("| ➖ 不适用 | 命中已知数据冲突（见下节），两个答案都算对 |")
    w("")
    w("有一点要说清楚：**答「不知道」和答错，是两件事**。"
      "知识库里没有就说没有，这是正确行为；编一个数字出来才是错的。"
      "拒答分析那一页就是按这个逻辑把「正确拒答」和「知识缺口」分开的。")
    w("")

    # ── 三、已知数据冲突 ──
    conflicts = data.get("known_conflicts") or []
    if conflicts:
        w("---")
        w("")
        w("## 三、已知的数据源冲突（不算答错，但要在结果里标注）")
        w("")
        w("资料包里的同一件事在两个地方写了不同的数字。这不是数字人的问题，"
          "但如果结果文档里不说明，看起来就像它答错了。")
        w("")
        w("| 编号 | 位置 | 冲突内容 | 判定规则 |")
        w("|---|---|---|---|")
        for c in conflicts:
            w(f"| `{c['id']}` | {c['item']} | {c['detail']} | {c['rule']} |")
        w("")
        w("> 顺带说，这两条冲突本身也是发现：说明资料包的「结构化字段」"
          "和「专题表」是两套来源，灌进知识库之前没做过一致性检查。"
          "换景区时要提醒提供方核对。")
        w("")

    # ── 四、全部题目 ──
    w("---")
    w("")
    w("## 四、全部题目")
    w("")
    for c in cats:
        group = by_cat.get(c["code"], [])
        w(f"### {c['name']}（{c['code']}）· {len(group)} 条")
        w("")
        w(f"*测什么：{c['what']}*")
        w("")
        w("| 题号 | 问题 | 期望要点（核对基准） | 备注 |")
        w("|---|---|---|---|")
        for it in group:
            q = it["q"].replace("|", "\\|")
            exp = it["expect"].replace("|", "\\|")
            note = (it.get("note") or "").replace("|", "\\|")
            w(f"| `{it['id']}` | {q} | {exp} | {note} |")
        w("")

        turns = [it for it in group if it.get("turns")]
        if turns:
            w("**多轮脚本展开**（上面表格里是压缩写法）：")
            w("")
            for it in turns:
                w(f"- `{it['id']}`：" + " → ".join(
                    f"{n}. {t}" for n, t in enumerate(it["turns"], 1)))
            w("")

    # ── 五、怎么用 ──
    w("---")
    w("")
    w("## 五、这些题怎么用")
    w("")
    w("1. 跑一遍全部题目，把回答记进 `test-results.md`（配套文档，逐条对应题号）。")
    w("2. 按上表的判定口径逐条核对，填「判定」和「备注」。")
    w("3. 汇总出几组数字放进论文：事实命中率、关系题命中率、概览覆盖度、"
      "边界判定正确率、缺口识别的编造率、记忆正确率。")
    w("4. 边界题和缺口题的失败案例单独列出来——它们指向的是规则要调，"
      "不是知识要补，这两类问题最容易互相冒充。")
    w("")
    w("> 顺序上建议：先跑事实型，因为它最稳；边界题和多轮题放最后，"
      "因为它们会往对话记录里写东西，影响后面统计。")
    w("")

    return "\n".join(out) + "\n"


def main() -> int:
    _fix_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验条数，不写文件")
    args = ap.parse_args()

    data = load()
    problems = validate(data)
    if problems:
        print("校验不通过：")
        for p in problems:
            print("  -", p)
        return 1

    counts = Counter(i["cat"] for i in data["items"])
    print("校验通过：", "  ".join(f"{k}={v}" for k, v in sorted(counts.items())),
          f"  合计={len(data['items'])}")

    if args.check:
        return 0

    OUT_FILE.write_text(render(data), encoding="utf-8")
    print("已写出：", OUT_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
