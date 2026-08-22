"""Supplier resolution.

Paying the wrong supplier is worse than paying the wrong amount, so this module
resolves twice -- by registration number and by name -- and refuses to pick a
winner when the two disagree.
"""

import pytest

from app.models import MatchMethod
from app.pipeline.partners import normalize_company_name, normalize_registration_no


def test_legal_name_and_registration_number_agree(master):
    m = master.resolve("株式会社山田製作所", "T1010001000101")
    assert (m.partner_code, m.method, m.confidence) == ("P-1001", MatchMethod.REGISTRATION_NO, 1.0)


def test_katakana_alias_still_resolves(master):
    """invoice_06 prints ヤマダ製作所, which is only in the master's alias list."""
    m = master.resolve("ヤマダ製作所", "T1010001000101")
    assert m.partner_code == "P-1001"
    m_no_reg = master.resolve("ヤマダ製作所", None)
    assert (m_no_reg.partner_code, m_no_reg.method) == ("P-1001", MatchMethod.ALIAS)


def test_supplier_absent_from_master_does_not_resolve(master):
    """invoice_10. There is no partner_code to post against, so it cannot be registered."""
    m = master.resolve("新星ロジスティクス株式会社", "T9090009000909")
    assert not m.resolved
    assert m.method is MatchMethod.UNRESOLVED


def test_name_and_registration_conflict_resolves_to_nothing(master):
    """A conflict is a red flag, not a tie to be broken."""
    m = master.resolve("有限会社佐藤商店", "T1010001000101")
    assert m.partner_code is None
    assert m.agreement is False
    assert "P-1001" in m.detail["conflict"] and "P-1002" in m.detail["conflict"]


def test_registration_number_wins_when_name_is_unreadable(master):
    m = master.resolve("", "T3030003000303")
    assert m.partner_code == "P-1003"
    assert m.confidence < 1.0  # uncorroborated by name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("株式会社山田製作所", "山田製作所"),
        ("㈱山田製作所", "山田製作所"),
        ("(株)山田製作所", "山田製作所"),
        ("有限会社佐藤商店", "佐藤商店"),
        ("東京フーズ株式会社 御中", "東京フーズ"),
        ("大阪機械工業　株式会社", "大阪機械工業"),
    ],
)
def test_legal_form_noise_is_stripped(raw, expected):
    assert normalize_company_name(raw) == expected


def test_registration_numbers_fold_consistently():
    assert normalize_registration_no("t-1010-0010-00101") == "T1010001000101"
    assert normalize_registration_no("Ｔ1010001000101") == "T1010001000101"
    assert normalize_registration_no(None) == ""
