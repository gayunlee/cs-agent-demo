"""Domain SSoT — YAML 기반 환불 정책/템플릿/조건 체인.

Gayoon 이전 프로젝트(visibility_chain.yaml)의 설계 원칙 차용:
- YAML이 Single Source of Truth
- Turing-incomplete DSL (field.path == 'VALUE' 등)
- Rule ID 재사용 (여러 체인에서 subset)
- fail_message 템플릿 치환
- api/tool 호출 명시

구성:
- dsl.py: 표현식 평가기 (Turing-incomplete)
- loader.py: YAML 로더 + 캐시
- diagnose_engine.py: 체인 순회 + first_failure 반환
- action_harness.py: tool pre/post validation 헬퍼
"""
