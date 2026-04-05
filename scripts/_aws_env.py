"""Helper — us-product-agent 의 .env 에서 AWS credentials 를 프로세스 env 로 로드.

cs-agent-demo 는 별도 .env 를 만들지 않고, 같은 AWS 계정이므로 이웃 프로젝트의
것을 재사용. 이 모듈을 import 하면 부수효과로 os.environ 이 채워짐.
"""
from __future__ import annotations

import os
from pathlib import Path

US_PRODUCT_ENV = Path.home() / "Documents/ai/us-product-agent/.env"
_AWS_KEYS = ("AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def load_aws_env() -> None:
    if not US_PRODUCT_ENV.exists():
        return
    for line in US_PRODUCT_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in _AWS_KEYS and key not in os.environ:
            os.environ[key] = val


# import 시 자동 로드
load_aws_env()
