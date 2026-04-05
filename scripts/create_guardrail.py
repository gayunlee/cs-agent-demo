"""Bedrock Guardrail 생성 — cs-agent-demo.

us-product-agent 는 AWS 콘솔에서 수동 생성했지만, 우리는 boto3 로 스크립트.

정책 (minimal 데모):
- PII 마스킹: 카드번호, 전화, 이메일 → ANONYMIZE
- 나머지 (topic filter / content filter / prompt attack) 는 현재 쇼케이스 불필요
- 리전: wrapper BedrockModel 과 동일한 us-west-2

결과: guardrail_id.json 에 id + version 저장.

실행:
  .venv311/bin/python -m scripts.create_guardrail
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# us-product-agent .env 에서 AWS credentials 자동 로드
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import _aws_env  # noqa: F401

import boto3

REGION = "us-west-2"  # wrapper_agent.py 의 BedrockModel 과 동일
NAME = "cs_agent_guardrail"
DESCRIPTION = "CS agent 답변의 PII 마스킹 (카드번호/전화/이메일)"

# 모델 차단/ 유저-차단 공통 메시지
BLOCKED_MSG = "요청 내용에 민감정보가 포함되어 답변이 제한되었습니다. 상담사에게 전달드릴게요."


def main():
    client = boto3.client("bedrock", region_name=REGION)

    print("=" * 60)
    print("Bedrock Guardrail 생성")
    print("=" * 60)
    print(f"  region:      {REGION}")
    print(f"  name:        {NAME}")
    print(f"  PII entities: CREDIT_DEBIT_CARD_NUMBER, PHONE, EMAIL (ANONYMIZE)")
    print("=" * 60)

    resp = client.create_guardrail(
        name=NAME,
        description=DESCRIPTION,
        blockedInputMessaging=BLOCKED_MSG,
        blockedOutputsMessaging=BLOCKED_MSG,
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
                {"type": "PHONE", "action": "ANONYMIZE"},
                {"type": "EMAIL", "action": "ANONYMIZE"},
            ],
        },
    )

    guardrail_id = resp["guardrailId"]
    version = resp.get("version", "DRAFT")

    # DRAFT 버전을 published 버전으로 create_guardrail_version 호출
    ver_resp = client.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="v1 minimal PII masking",
    )
    published_version = ver_resp["version"]

    out_path = Path(__file__).resolve().parents[1] / "guardrail_id.json"
    out_path.write_text(
        json.dumps(
            {
                "guardrailId": guardrail_id,
                "version": published_version,
                "name": NAME,
                "region": REGION,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n✅ 생성 완료")
    print(f"   guardrail_id: {guardrail_id}")
    print(f"   version:      {published_version}")
    print(f"   saved to:     {out_path}")


if __name__ == "__main__":
    main()
