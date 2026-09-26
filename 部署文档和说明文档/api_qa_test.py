import json
import time
import urllib.error
import urllib.request


BASE_URL = "http://127.0.0.1:8000"
ENDPOINT = "/api/graph/qa/"

QUESTIONS = [
    "灵山大佛有多高？",
    "九龙灌浴表演主要看点是什么？",
    "梵宫的参观重点是什么？",
    "半天时间游览灵山，推荐怎么走？",
    "亲子游客适合优先看哪些景点？",
]


def post_json(path, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        body = response.read().decode("utf-8")
        return response.status, json.loads(body), elapsed_ms


def main():
    print("API 问答批量测试")
    print(f"接口：{BASE_URL}{ENDPOINT}")
    print()

    results = []
    for index, question in enumerate(QUESTIONS, start=1):
        print(f"[{index}] 问题：{question}")
        try:
            status, payload, elapsed_ms = post_json(ENDPOINT, {"question": question})
            answer = payload.get("answer", "")
            evidence = payload.get("evidence") or []
            entities = payload.get("entities") or []
            graph_stats = payload.get("graph_stats") or {}

            print(f"HTTP：{status}")
            print(f"耗时：{elapsed_ms} ms")
            print(f"回答：{answer}")
            print(f"实体数：{len(entities)}")
            print(f"证据链：{len(evidence)} 条")
            if graph_stats:
                print(f"图谱规模：{graph_stats.get('nodes')} 个实体 / {graph_stats.get('links')} 条关系")
            print()

            results.append(
                {
                    "question": question,
                    "status": status,
                    "elapsed_ms": elapsed_ms,
                    "answer": answer,
                    "entities": len(entities),
                    "evidence": len(evidence),
                }
            )
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            print(f"HTTP 错误：{error.code}")
            print(body)
            print()
            results.append({"question": question, "status": error.code, "error": body})
        except Exception as error:
            print(f"请求失败：{error}")
            print()
            results.append({"question": question, "status": "ERROR", "error": str(error)})

    ok_count = sum(1 for item in results if item.get("status") == 200)
    print("测试完成")
    print(f"成功返回：{ok_count}/{len(results)}")
    print()
    print("说明：脚本只统计接口是否成功返回；事实正确性需按回答内容和证据链人工判定。")


if __name__ == "__main__":
    main()
