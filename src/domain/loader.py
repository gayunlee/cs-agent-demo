"""YAML SSoT 로더.

도메인 파일들을 로드하고 캐시. 변경 감지는 mtime 기반.

파일:
- domain/refund_chains.yaml: 조건 체인 (DiagnoseEngine이 순회)
- domain/templates.yaml: 16종 답변 템플릿
- domain/refund_rules.yaml: 용어/톤 규칙 (Domain knowledge)
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

import yaml

DOMAIN_DIR = Path(__file__).parent.parent.parent / "domain"


class DomainLoader:
    """YAML 파일 로더 + mtime 캐시."""

    def __init__(self, domain_dir: Path | str = DOMAIN_DIR):
        self.domain_dir = Path(domain_dir)
        self._cache: dict[str, tuple[float, Any]] = {}

    def load(self, filename: str) -> dict:
        """domain/{filename} 로드. mtime 캐시 활용."""
        path = self.domain_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Domain file not found: {path}")

        mtime = path.stat().st_mtime
        cached = self._cache.get(filename)
        if cached and cached[0] == mtime:
            return cached[1]

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._cache[filename] = (mtime, data)
        return data

    def load_chains(self) -> dict:
        """refund_chains.yaml 로드 → {chain_id: chain_dict}"""
        data = self.load("refund_chains.yaml")
        return data.get("chains", {})

    def load_templates(self) -> dict:
        """templates.yaml 로드 → {template_id: template_dict}"""
        data = self.load("templates.yaml")
        return data.get("templates", {})

    def get_chain(self, chain_id: str) -> dict | None:
        """특정 chain 조회"""
        return self.load_chains().get(chain_id)

    def get_template(self, template_id: str) -> dict | None:
        """특정 template 조회"""
        return self.load_templates().get(template_id)

    def clear_cache(self):
        self._cache.clear()


# 싱글턴 인스턴스
_default_loader: DomainLoader | None = None


def get_loader() -> DomainLoader:
    global _default_loader
    if _default_loader is None:
        _default_loader = DomainLoader()
    return _default_loader
