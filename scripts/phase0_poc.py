"""Phase 0 PoC: Strands Agent + Bedrock Haiku 4.5 + 1 tool.

목적:
  - Strands SDK API가 조사 결과와 일치하는지 검증
  - @tool 데코레이터, Agent 초기화, BedrockModel 연결 확인
  - Bedrock Haiku 4.5로 tool-use loop 동작 검증
"""
from dotenv import load_dotenv
load_dotenv()

from strands import Agent, tool
from strands.models import BedrockModel


@tool
def get_refund_policy_summary(product_type: str) -> str:
    """환불 정책 요약을 반환합니다.

    Args:
        product_type: 'subscription' 또는 'onetime'
    """
    if product_type == "subscription":
        return (
            "구독 상품 환불 정책:\n"
            "- 7일 이내: 전액 환불\n"
            "- 7일 경과: 부분 환불 (수수료 10% + 사용일수 차감)\n"
            "- 환불 가능 기간: 결제일로부터 6개월"
        )
    return "단건 상품: 결제 후 7일 이내 환불 가능"


def main():
    model = BedrockModel(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        region_name="us-west-2",
    )

    agent = Agent(
        model=model,
        tools=[get_refund_policy_summary],
        system_prompt=(
            "당신은 한국 교육 SaaS의 CS 상담사입니다. "
            "유저 질문에 공감 표현 후 정책을 안내하세요. 답변은 간결하게."
        ),
    )

    print("=" * 60)
    print("Phase 0 PoC — Strands + Bedrock Haiku 4.5")
    print("=" * 60)
    print("\n[유저 메시지] 6개월 구독 상품 환불 규정 알려주세요\n")

    result = agent("6개월 구독 상품 환불 규정 알려주세요")

    print("\n=== AGENT RESULT ===")
    print(result)
    print("\n=== RESULT TYPE ===")
    print(type(result))
    print("\n✅ Phase 0 PoC 성공")


if __name__ == "__main__":
    main()
