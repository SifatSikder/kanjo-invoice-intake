"""Resolve the supplier printed on an invoice to a partner_code in the master.

Only suppliers in `GET /partners` can be registered, so this step decides whether
an invoice is postable at all. It is also the step with the worst failure mode:
a wrong amount costs money once, a wrong *payee* sends money to the wrong company.

So we resolve twice, independently, and require agreement:

  1. by 登録番号 (invoice registration number) -- a 13-digit national identifier
     printed on every qualified invoice and stored against every partner. Exact,
     unambiguous, and immune to the naming variation below.
  2. by company name, after stripping the legal-form noise that makes
     "株式会社山田製作所", "山田製作所" and "ヤマダ製作所" three spellings of one company.

When both resolve and disagree, that is not a low-confidence match -- it is a red
flag, and it goes to a human.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.models import MatchMethod

# Legal-form prefixes/suffixes carry no identifying information and are written
# inconsistently (株式会社 / (株) / ㈱ / KK). Strip them before comparing.
_LEGAL_FORMS = (
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "公益社団法人", "一般財団法人", "医療法人", "学校法人",
    "㈱", "㈲", "(株)", "(有)", "（株）", "（有）",
)
_HONORIFICS = ("御中", "様", "殿")
_WHITESPACE = re.compile(r"[\s　]+")
_REG_NOISE = re.compile(r"[^0-9A-Z]")


def normalize_registration_no(raw: str | None) -> str:
    """T1010001000101 / t-1010-0010-00101 / Ｔ１０１... all fold to one form."""
    if not raw:
        return ""
    folded = unicodedata.normalize("NFKC", str(raw)).upper()
    return _REG_NOISE.sub("", folded)


def normalize_company_name(raw: str | None) -> str:
    """Fold a printed company name to a comparable key.

    Katakana/kanji variants are *not* unified here -- ヤマダ and 山田 stay
    different strings. That is intentional: the master's `aliases` list is the
    business's own statement of which spellings mean the same company, and
    inventing our own transliteration rules would be guessing on their behalf.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    for honorific in _HONORIFICS:
        text = text.replace(honorific, "")
    for form in _LEGAL_FORMS:
        text = text.replace(form, "")
    text = _WHITESPACE.sub("", text)
    return text.strip().lower()


@dataclass(frozen=True)
class Partner:
    partner_code: str
    name: str
    aliases: tuple[str, ...]
    registration_no: str

    @property
    def name_keys(self) -> set[str]:
        keys = {normalize_company_name(self.name)}
        keys.update(normalize_company_name(a) for a in self.aliases)
        return {k for k in keys if k}


@dataclass
class PartnerMatch:
    partner_code: str | None = None
    method: MatchMethod = MatchMethod.UNRESOLVED
    confidence: float = 0.0
    by_registration: str | None = None
    by_name: str | None = None
    agreement: bool = True
    detail: dict = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.partner_code is not None


class PartnerMaster:
    """The supplier master, fetched from the accounting system -- never hardcoded."""

    def __init__(self, partners: list[dict]) -> None:
        self.partners: list[Partner] = [
            Partner(
                partner_code=p["partner_code"],
                name=p["name"],
                aliases=tuple(p.get("aliases") or ()),
                registration_no=p.get("registration_no") or "",
            )
            for p in partners
        ]
        self._by_reg: dict[str, Partner] = {
            normalize_registration_no(p.registration_no): p
            for p in self.partners
            if p.registration_no
        }
        self._by_name: dict[str, Partner] = {}
        for partner in self.partners:
            for key in partner.name_keys:
                self._by_name.setdefault(key, partner)

    def __len__(self) -> int:
        return len(self.partners)

    def get(self, partner_code: str | None) -> Partner | None:
        if not partner_code:
            return None
        return next((p for p in self.partners if p.partner_code == partner_code), None)

    def resolve(
        self,
        name: str | None,
        registration_no: str | None = None,
        *,
        fuzzy_threshold: float = 85.0,
    ) -> PartnerMatch:
        detail: dict = {
            "supplier_name_read": name or "",
            "registration_no_read": registration_no or "",
        }

        # --- path 1: registration number, exact ------------------------------
        reg_key = normalize_registration_no(registration_no)
        reg_partner = self._by_reg.get(reg_key) if reg_key else None
        detail["registration_no_normalized"] = reg_key

        # --- path 2: name, exact then alias then fuzzy -----------------------
        name_key = normalize_company_name(name)
        detail["name_normalized"] = name_key
        name_partner = self._by_name.get(name_key) if name_key else None
        name_method = MatchMethod.UNRESOLVED
        name_confidence = 0.0

        if name_partner:
            name_method = (
                MatchMethod.EXACT_NAME
                if normalize_company_name(name_partner.name) == name_key
                else MatchMethod.ALIAS
            )
            name_confidence = 0.95 if name_method is MatchMethod.EXACT_NAME else 0.90
        elif name_key:
            best, best_score = None, 0.0
            for partner in self.partners:
                for key in partner.name_keys:
                    score = fuzz.token_set_ratio(name_key, key)
                    if score > best_score:
                        best, best_score = partner, score
            detail["fuzzy_best_score"] = round(best_score, 1)
            if best and best_score >= fuzzy_threshold:
                name_partner = best
                name_method = MatchMethod.FUZZY_NAME
                # Capped below 1.0 on purpose: a fuzzy supplier match always
                # goes to a human, however high the string similarity.
                name_confidence = 0.60

        by_reg = reg_partner.partner_code if reg_partner else None
        by_name = name_partner.partner_code if name_partner else None
        detail["by_registration"] = by_reg
        detail["by_name"] = by_name

        # --- reconcile -------------------------------------------------------
        agreement = True
        if by_reg and by_name and by_reg != by_name:
            agreement = False
            detail["conflict"] = (
                f"registration number points to {by_reg} but the printed name "
                f"points to {by_name}"
            )
            # Trust neither. A conflict here is a fraud/misfile signal.
            return PartnerMatch(
                partner_code=None,
                method=MatchMethod.UNRESOLVED,
                confidence=0.0,
                by_registration=by_reg,
                by_name=by_name,
                agreement=False,
                detail=detail,
            )

        if reg_partner:
            # Corroborated by name as well -> full confidence.
            confidence = 1.0 if by_name == by_reg else 0.92
            return PartnerMatch(
                partner_code=reg_partner.partner_code,
                method=MatchMethod.REGISTRATION_NO,
                confidence=confidence,
                by_registration=by_reg,
                by_name=by_name,
                agreement=agreement,
                detail=detail,
            )

        if name_partner:
            return PartnerMatch(
                partner_code=name_partner.partner_code,
                method=name_method,
                confidence=name_confidence,
                by_registration=None,
                by_name=by_name,
                agreement=agreement,
                detail=detail,
            )

        detail["reason"] = "supplier is not in the partner master"
        return PartnerMatch(detail=detail)
