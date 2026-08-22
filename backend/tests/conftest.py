"""Shared fixtures. Everything here runs offline -- no LLM, no database, no API."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.pipeline.partners import PartnerMaster

REPO_ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH = REPO_ROOT / "evals" / "ground_truth.yaml"

# The supplier master exactly as the mock accounting API serves it. Copied here
# so the suite does not need the API running; app code always fetches it live.
PARTNER_FIXTURE = [
    {"partner_code": "P-1001", "name": "株式会社山田製作所",
     "aliases": ["ヤマダ製作所", "山田製作所"], "registration_no": "T1010001000101"},
    {"partner_code": "P-1002", "name": "有限会社佐藤商店",
     "aliases": ["佐藤商店"], "registration_no": "T2020002000202"},
    {"partner_code": "P-1003", "name": "東京フーズ株式会社",
     "aliases": ["東京フーズ"], "registration_no": "T3030003000303"},
    {"partner_code": "P-1004", "name": "大阪機械工業株式会社",
     "aliases": ["大阪機械", "大阪機械工業"], "registration_no": "T4040004000404"},
    {"partner_code": "P-1005", "name": "みらいITソリューションズ株式会社",
     "aliases": ["みらいIT", "みらいITソリューションズ"], "registration_no": "T5050005000505"},
]


@pytest.fixture(scope="session")
def master() -> PartnerMaster:
    return PartnerMaster(PARTNER_FIXTURE)


@pytest.fixture(scope="session")
def ground_truth() -> dict:
    with GROUND_TRUTH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
