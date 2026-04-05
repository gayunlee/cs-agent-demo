"""AgentCore Memory resource 생성 (Seoul 리전).

us-product-agent 패턴 복사:
- bedrock_agentcore.memory.controlplane.MemoryControlPlaneClient.create_memory()
- 결과 id 를 프로젝트 루트의 memory_id.json 에 저장
- .env 는 us-product-agent 의 AWS credentials 를 읽음 (계정 공유)

실행:
  .venv311/bin/python -m scripts.create_memory_resource
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# us-product-agent .env 에서 AWS credentials 만 읽어 사용
US_PRODUCT_ENV = Path.home() / "Documents/ai/us-product-agent/.env"


def load_aws_creds_from_neighbor() -> None:
    if not US_PRODUCT_ENV.exists():
        print(f"❌ {US_PRODUCT_ENV} 없음. AWS credentials 확보 실패.")
        sys.exit(1)
    for line in US_PRODUCT_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in ("AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            os.environ[key] = val


def main():
    load_aws_creds_from_neighbor()
    # 서울 리전 고정 (us-product-agent 와 동일)
    region = os.environ.get("AWS_REGION", "ap-northeast-2")

    from bedrock_agentcore.memory.controlplane import MemoryControlPlaneClient

    name = "cs_agent_memory"
    description = "CS agent wrapper 멀티턴 맥락 저장소 (환불/해지 상담). 채널톡 상담은 보통 2일 내 종료, 14일은 안전 버퍼."
    event_expiry_days = 14

    print("=" * 60)
    print("AgentCore Memory resource 생성")
    print("=" * 60)
    print(f"  region:            {region}")
    print(f"  name:              {name}")
    print(f"  event_expiry_days: {event_expiry_days}")
    print(f"  description:       {description}")
    print("=" * 60)

    client = MemoryControlPlaneClient(region_name=region)
    memory = client.create_memory(
        name=name,
        event_expiry_days=event_expiry_days,
        description=description,
        wait_for_active=True,
    )

    memory_id = memory.get("id") or memory.get("memoryId") or memory.get("Id")
    if not memory_id:
        print("❌ 응답에 memory id 없음:", json.dumps(memory, default=str, indent=2))
        sys.exit(2)

    out_path = Path(__file__).resolve().parents[1] / "memory_id.json"
    out_path.write_text(
        json.dumps({"memoryId": memory_id, "name": name, "region": region}, indent=2),
        encoding="utf-8",
    )

    print(f"\n✅ 생성 완료")
    print(f"   memory_id: {memory_id}")
    print(f"   saved to:  {out_path}")


if __name__ == "__main__":
    main()
