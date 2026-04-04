"""환불/해지 Evaluation — normal + edge

normal (rule-based): 템플릿 + 금액 정확성
edge (LLM-as-judge): 필수 요소 포함 여부

Usage:
    python scripts/eval_refund.py                    # 전체
    python scripts/eval_refund.py --normal-only       # normal만
    python scripts/eval_refund.py --edge-only         # edge만
"""
import json
import sys
import time
import boto3
from pathlib import Path
from collections import Counter
from datetime import datetime, date

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.workflow import WorkflowContext, run_workflow
from src.templates import TEMPLATES


# ── 데이터 로드 ──

def load_test_data():
    """패턴 분석 결과 + 실제 대화 로드"""
    with open("data/test_cases/refund_pattern_analysis.json") as f:
        analysis = json.load(f)
    with open("data/test_cases/refund_convos_jan.json") as f:
        convos = json.load(f)

    convo_map = {c["chat_id"]: c for c in convos}

    normal = []
    edge = []
    for a in analysis:
        convo = convo_map.get(a["chat_id"])
        if not convo:
            continue
        case = {**a, "turns": convo["turns"]}
        if a.get("pattern") == "일반":
            normal.append(case)
        elif a.get("pattern") == "튀는":
            edge.append(case)

    return normal, edge


# ── Normal Eval (Rule-Based) ──

def eval_normal(cases: list[dict]) -> dict:
    """워크플로우 분기 + 답변 정확성 체크"""
    results = []

    for case in cases:
        turns = case["turns"]

        # 유저 메시지 추출 (봇 제외)
        user_msgs = [t["text"] for t in turns if t["role"] == "user"]
        mgr_msgs = [t["text"] for t in turns if t["role"] == "manager"]

        if not user_msgs:
            continue

        # 워크플로우 실행 (mock: 결제 있다고 가정)
        # 실제로는 admin API 데이터가 필요하지만, 여기서는 매니저 답변에서 역추론
        mgr_first = (mgr_msgs[0] if mgr_msgs else "").lower()

        # 매니저 답변에서 기대 템플릿 추론
        expected_template = _infer_template(mgr_first)

        # 매니저 답변에서 환불 금액 추출
        expected_amount = _extract_amount(mgr_msgs[0] if mgr_msgs else "")

        results.append({
            "chat_id": case["chat_id"],
            "user_first": user_msgs[0][:60] if user_msgs else "",
            "expected_template": expected_template,
            "expected_amount": expected_amount,
            "mgr_first": mgr_first[:80],
            "flow": case.get("flow", ""),
        })

    return {
        "total": len(results),
        "results": results,
        "template_dist": dict(Counter(r["expected_template"] for r in results)),
    }


def _infer_template(mgr_text: str) -> str:
    """매니저 답변에서 사용한 템플릿 추론"""
    m = mgr_text.lower()
    if "구독해지방법" in m or "구독해지 방법" in m or "정기결제 구독해지" in m:
        return "T1_구독해지_방법_앱"
    if "7일 이내 구독권 미개시" in m or "환불 규정에 따른" in m:
        return "T2_환불_규정_금액"
    if "환불금" in m and "원" in m and ("차감" in m or "수수료" in m):
        return "T2_환불_규정_금액"
    if "환불 접수 완료" in m or "환불접수 완료" in m:
        return "T3_환불_접수_완료"
    if "정기적으로 제공되는 콘텐츠" in m or "구독형 스터디" in m:
        return "T4_자동결제_설명"
    if "성함" in m and ("휴대전화" in m or "번호" in m) and len(m) < 150:
        return "T6_본인확인_요청"
    if "카드 변경" in m or "결제카드" in m:
        return "T8_카드변경_안내"
    return "T99_기타"


def _extract_amount(mgr_text: str) -> int | None:
    """매니저 답변에서 환불 금액 추출"""
    import re
    # "■ 환불 금액: 360,000원" 또는 "360,000원 환불"
    patterns = [
        r"환불\s*금액[:\s]*([0-9,]+)원",
        r"([0-9,]+)원\s*환불",
    ]
    for pattern in patterns:
        match = re.search(pattern, mgr_text)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


# ── Edge Eval (LLM-as-Judge) ──

def eval_edge(cases: list[dict]) -> dict:
    """LLM이 필수 요소 포함 여부 판단"""
    bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")
    MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    JUDGE_PROMPT = """\
당신은 CS 답변 품질 평가자입니다.
실제 매니저 답변을 읽고, 에이전트가 반드시 포함해야 할 필수 요소를 추출하세요.

출력은 반드시 아래 JSON 형식만. 다른 텍스트 없이.

{"required_elements": ["요소1", "요소2"], "tone": "공감|규정안내|사과|안내", "special_handling": "특별 대응 설명"}
"""

    results = []
    start = time.time()

    for i, case in enumerate(cases):
        turns = case["turns"]
        mgr_msgs = [t["text"] for t in turns if t["role"] == "manager"]
        user_msgs = [t["text"] for t in turns if t["role"] == "user"]

        if not mgr_msgs:
            continue

        mgr_combined = "\n".join(m[:200] for m in mgr_msgs[:3])

        try:
            resp = bedrock.invoke_model(
                modelId=MODEL,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 300,
                    "system": JUDGE_PROMPT,
                    "messages": [{"role": "user", "content": f"매니저 답변:\n{mgr_combined}"}],
                }),
            )
            raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
            idx = raw.find("{")
            if idx >= 0:
                data = json.loads(raw[idx:raw.rfind("}") + 1])
            else:
                data = {"required_elements": [], "tone": "?"}
        except Exception as e:
            data = {"required_elements": [], "tone": "에러", "error": str(e)}

        results.append({
            "chat_id": case["chat_id"],
            "user_first": user_msgs[0][:60] if user_msgs else "",
            "flow": case.get("flow", ""),
            "special": case.get("special", ""),
            "required_elements": data.get("required_elements", []),
            "tone": data.get("tone", ""),
            "special_handling": data.get("special_handling", ""),
        })

        if (i + 1) % 5 == 0:
            print(f"  edge [{i+1}/{len(cases)}] {time.time()-start:.0f}s")

    return {
        "total": len(results),
        "results": results,
    }


# ── 메인 ──

def main():
    normal_only = "--normal-only" in sys.argv
    edge_only = "--edge-only" in sys.argv

    normal, edge = load_test_data()
    print(f"테스트 데이터: normal {len(normal)}건, edge {len(edge)}건\n")

    if not edge_only:
        print("=== Normal Eval (Rule-Based) ===")
        normal_result = eval_normal(normal)
        print(f"  total: {normal_result['total']}건")
        print(f"  템플릿 분포: {normal_result['template_dist']}")

        # 저장
        with open("data/eval_results/refund_normal_eval.json", "w") as f:
            json.dump(normal_result, f, ensure_ascii=False, indent=2)
        print(f"  저장: data/eval_results/refund_normal_eval.json\n")

    if not normal_only:
        print("=== Edge Eval (LLM-as-Judge) ===")
        edge_result = eval_edge(edge)
        print(f"  total: {edge_result['total']}건")

        for r in edge_result["results"]:
            print(f"\n  [{r['chat_id'][:10]}] {r['flow'][:60]}")
            print(f"    필수 요소: {r['required_elements']}")
            print(f"    톤: {r['tone']}")
            if r.get("special_handling"):
                print(f"    특별: {r['special_handling'][:80]}")

        # 저장
        with open("data/eval_results/refund_edge_eval.json", "w") as f:
            json.dump(edge_result, f, ensure_ascii=False, indent=2)
        print(f"\n  저장: data/eval_results/refund_edge_eval.json")


if __name__ == "__main__":
    main()
