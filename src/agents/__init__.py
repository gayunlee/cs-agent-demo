"""Strands Agents — 상담 Agent 단일 에이전트 하이브리드.

구조:
- consultant.py: 메인 상담 Agent (Strands Agent + 14 tools + SlidingWindow)

원칙:
- 단일 에이전트 (multi-agent 아님)
- Tool catalog 평탄 (4 카테고리: data / workflow / conversation / fallback)
- System prompt는 대화 톤 + tool 가이드만 (정책 규칙은 YAML로)
- Harness는 각 tool 내부에 pre/post validation
"""
