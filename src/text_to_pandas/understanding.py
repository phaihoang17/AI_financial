"""Deterministic question understanding and dataset discovery for Text-to-Pandas (ADR-069).

Understanding reuses the existing NLU resolvers (company alias resolution,
statement scope, operation detection, temporal quarter detection, requested
scale) and adds what a DataFrame query needs: several companies, year ranges,
every catalog metric and ratio, and ranking cues. It never calls a model.

Discovery then decides, before any code generation, whether the question can
be answered from the catalog and which rows the generated code may read:

- company mode: every row of the named companies;
- screening mode (no company): the rows of the named metrics for all companies.

It abstains or asks for clarification with a typed reason when a required
company, period, scope or metric is missing or unavailable, instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from src.formulas.ratios import RATIOS, RATIOS_BY_ID
from src.text_to_pandas.catalog import DatasetCatalog, DatasetSlice, metric_dataset, schema_context
from src.text_to_pandas.metrics import METRICS, fold
from src.understanding.company_resolver import CompanyAlias, CompanyResolutionStatus, resolve_company
from src.understanding.operation_detector import OperationDetectorInput, detect_operation
from src.understanding.requested_scale_unit_parser import RequestedScaleUnitParserInput, parse_requested_scale_unit
from src.understanding.schemas import PeriodKind
from src.understanding.statement_scope_resolver import (
    StatementScopeResolutionStatus,
    StatementScopeResolverInput,
    resolve_statement_scope,
)
from src.understanding.temporal_parser import TemporalParserInput, parse_periods


class GateDecision(str, Enum):
    PROCEED = "PROCEED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"
    NO_DATA = "NO_DATA"


# 3-character upper-case tokens that are vocabulary, not tickers.
_NON_TICKER_TOKENS = frozenset({
    "ROE", "ROA", "EPS", "VND", "USD", "CFO", "CFI", "CFF", "PBT", "VAS", "TOP", "CEO", "CPI", "GDP",
    "BCTC", "TNDN", "LNST", "LNTT", "COGS", "ROS", "ROIC", "EBIT", "NIM", "CAR", "NPL", "TCTD", "CTCP",
    "AND", "THE", "FOR", "QUY", "NAM", "TMCP", "TNHH", "VAT", "IPO", "ESG", "API", "SQL",
})
_TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{2})(?![A-Za-z0-9])")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_YEAR_RANGE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:-|–|đến|den|tới|toi|to)\s*(?:năm\s*|nam\s*)?((?:19|20)\d{2})(?!\d)")
# Concepts the statement datasets cannot contain (market data).
_UNAVAILABLE = (
    ("p/e", "P/E needs market price data"), ("p/b", "P/B needs market price data"),
    ("pe ratio", "P/E needs market price data"), ("gia co phieu", "share price data"),
    ("thi gia", "market price data"), ("von hoa", "market capitalisation data"),
    ("market cap", "market capitalisation data"), ("gia dong cua", "share price data"),
    ("khoi luong giao dich", "trading volume data"), ("share price", "share price data"),
    ("stock price", "share price data"), ("ev/ebitda", "enterprise value needs market data"),
)
_ALL_PERIOD_CUES = (
    "qua cac nam", "cac nam", "tung nam", "moi nam", "hang nam", "lich su", "xu huong", "nam nao",
    "giai doan", "toan bo", "tat ca cac nam", "theo nam", "nhung nam", "nhieu nam", "chuoi thoi gian",
    "time series", "each year", "all years", "over the years", "by year", "per year", "which year",
)
_GROWTH_CUES = ("tang truong", "tang bao nhieu", "giam bao nhieu", "bien dong", "growth", "thay doi bao nhieu")
_AVERAGE_CUES = ("trung binh", "average", "mean")
_RANKING_CUES = ("top", "cao nhat", "lon nhat", "thap nhat", "nho nhat", "xep hang", "nhieu nhat",
                 "it nhat", "highest", "lowest", "largest", "smallest", "rank")


@dataclass(frozen=True)
class CompanyMention:
    ticker: str
    name: str
    raw: str


@dataclass(frozen=True)
class T2PUnderstanding:
    question: str
    companies: tuple[CompanyMention, ...]
    unknown_tickers: tuple[str, ...]
    years: tuple[int, ...]
    all_periods: bool
    sub_annual_period: bool
    statement_scope: str | None
    scope_inferred: bool
    metrics: tuple[str, ...]
    ratios: tuple[str, ...]
    operation: str
    ranking: bool
    growth: bool
    average: bool
    requested_scale: str | None
    unavailable: tuple[str, ...]

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(company.ticker for company in self.companies)

    @property
    def required_metrics(self) -> tuple[str, ...]:
        """Detected metrics plus the components of detected ratios, in order."""
        result: list[str] = list(self.metrics)
        for ratio_id in self.ratios:
            for metric in RATIOS_BY_ID[ratio_id].required_metrics:
                if metric not in result:
                    result.append(metric)
        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "companies": [{"ticker": c.ticker, "name": c.name} for c in self.companies],
            "unknown_tickers": list(self.unknown_tickers),
            "fiscal_years": list(self.years),
            "all_periods": self.all_periods,
            "statement_scope": self.statement_scope,
            "metrics": list(self.metrics),
            "ratios": list(self.ratios),
            "operation": self.operation,
            "ranking": self.ranking,
            "growth": self.growth,
            "average": self.average,
            "requested_scale": self.requested_scale,
        }


def _phrase_matches(folded: str, phrase: str) -> list[tuple[int, int]]:
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(fold(phrase)) + r"(?![a-z0-9])")
    return [(match.start(), match.end()) for match in pattern.finditer(folded)]


# Wording variants of the same statement line or ratio, rewritten on folded text
# before matching (ADR-072): "lợi nhuận kế toán trước/sau thuế" and "lãi
# trước/sau thuế" are the profit lines, "tổng giá trị tài sản" is total assets,
# and "tỷ số"/"tỷ lệ" name the same coverage ratios as "hệ số". Interest lines
# ("lãi vay", "thu nhập lãi thuần") and per-share lines are not rewritten.
def _phrase(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])")


# Variant families, applied in order (later rules see earlier rewrites).
_CANONICAL_PHRASING = (
    # Profit lines: "lãi" = "lợi nhuận"; "kế toán" is optional; "ròng" is after tax.
    (_phrase(r"lai (gop|truoc thue|sau thue|rong)"), r"loi nhuan \1"),
    (_phrase(r"loi nhuan ke toan (truoc|sau) thue"), r"loi nhuan \1 thue"),
    (_phrase(r"(?<!ty suat )(?<!bien )loi nhuan rong"), "loi nhuan sau thue"),
    # Totals: "tổng giá trị tài sản", "quy mô tổng tài sản".
    (_phrase(r"(?:quy mo )?tong (?:gia tri )?tai san"), "tong tai san"),
    # Owners' equity shortened to "vốn chủ".
    (_phrase(r"von chu(?! so huu)"), "von chu so huu"),
    # Cash flows: "lưu chuyển tiền"/"dòng tiền", with or without "thuần"/"từ".
    (_phrase(r"(?:luu chuyen tien|dong tien)(?: thuan)?(?: (?:tu|trong|cua))? hoat dong (kinh doanh|dau tu|tai chinh)"),
     r"luu chuyen tien thuan tu hoat dong \1"),
    # Ratios: "tỷ số"/"tỷ lệ"/"chỉ số" = "hệ số"; "ngắn hạn" = "hiện hành" for liquidity.
    (_phrase(r"(?:ty so|ty le|chi so|he so|kha nang) thanh toan (?:hien hanh|ngan han)"), "he so thanh toan hien hanh"),
    (_phrase(r"(bien|ty suat) lai (gop|rong)"), r"\1 loi nhuan \2"),
    (_phrase(r"(?:(?:ty le|ty so|he so|chi so) )?no(?: phai tra)? ?(?:/|tren) ?(?:tong )?von chu so huu"),
     "no tren von chu so huu"),
    (_phrase(r"(?:ty suat |he so )?(?:loi nhuan(?: sau thue)?|sinh loi) tren (von chu so huu|tong tai san)"),
     r"ty suat loi nhuan tren \1"),
)
# A ratio acronym in parentheses names the description just before it
# ("lợi nhuận trên vốn chủ sở hữu bình quân (ROE)"), back to the clause start.
_CLAUSE_START = re.compile(r"[,.;:?!]\s*")


def canonical_phrasing(folded: str) -> str:
    for pattern, replacement in _CANONICAL_PHRASING:
        folded = pattern.sub(replacement, folded)
    return folded


def _described_ratio_spans(folded: str) -> list[tuple[int, int, str]]:
    spans = []
    for ratio in RATIOS:
        for phrase in ratio.synonyms:
            token = fold(phrase)
            if " " in token or "/" in token:
                continue  # only acronyms such as roe/roa
            for match in re.finditer(r"\(\s*" + re.escape(token) + r"\s*\)", folded):
                start = max((m.end() for m in _CLAUSE_START.finditer(folded, 0, match.start())), default=0)
                if folded[start:match.start()].strip():
                    spans.append((start, match.end(), ratio.ratio_id))
    return spans


def _detect_terms(folded: str) -> tuple[list[str], list[str], list[tuple[int, int]]]:
    """Metric and ratio ids named in the question, and the ratio-name spans.

    Longest phrases win and matches never overlap; a description followed by a
    ratio acronym in parentheses is that ratio's name.
    """
    candidates: list[tuple[int, int, str, str]] = []
    for metric in METRICS:
        for phrase in (metric.metric_id.lower().replace("_", " "), *metric.synonyms):
            for start, end in _phrase_matches(folded, phrase):
                candidates.append((start, end, "metric", metric.metric_id))
    for ratio in RATIOS:
        for phrase in ratio.synonyms:
            for start, end in _phrase_matches(folded, phrase):
                candidates.append((start, end, "ratio", ratio.ratio_id))
    for start, end, ratio_id in _described_ratio_spans(folded):
        candidates.append((start, end, "ratio", ratio_id))
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    taken: list[tuple[int, int]] = []
    metrics: list[tuple[int, str]] = []
    ratios: list[tuple[int, str]] = []
    ratio_spans: list[tuple[int, int]] = []
    for start, end, kind, identifier in candidates:
        if any(start < other_end and other_start < end for other_start, other_end in taken):
            continue
        taken.append((start, end))
        (metrics if kind == "metric" else ratios).append((start, identifier))
        if kind == "ratio":
            ratio_spans.append((start, end))

    def ordered(items):
        return list(dict.fromkeys(identifier for _, identifier in sorted(items)))

    return ordered(metrics), ordered(ratios), ratio_spans


def _years(question: str) -> list[int]:
    years: list[int] = []
    for match in _YEAR_RANGE.finditer(question):
        first, last = sorted((int(match.group(1)), int(match.group(2))))
        if last - first <= 30:
            years.extend(range(first, last + 1))
    years.extend(int(value) for value in _YEAR.findall(question))
    return sorted(dict.fromkeys(years))


def _companies(question: str, catalog: DatasetCatalog) -> tuple[list[CompanyMention], list[str]]:
    companies = catalog.companies
    aliases = [CompanyAlias(name, name, ticker)
               for ticker, name in companies[["ticker", "company_name"]].itertuples(index=False, name=None)]
    known = set(catalog.tickers())
    found: dict[str, CompanyMention] = {}
    unknown: list[str] = []
    folded = fold(question)
    occupied = []
    for alias in sorted(aliases, key=lambda item: -len(item.alias)):
        for start, end in _phrase_matches(folded, alias.alias):
            if any(start < other_end and other_start < end for other_start, other_end in occupied):
                continue
            resolution = resolve_company(alias.alias, aliases)
            if resolution.status is CompanyResolutionStatus.RESOLVED:
                company = resolution.company
                found.setdefault(company.ticker, CompanyMention(company.ticker, company.name, alias.alias))
                occupied.append((start, end))
    outside_names = list(folded)
    for start, end in occupied:
        outside_names[start:end] = " " * (end - start)
    outside_names = "".join(outside_names)
    for token in _TICKER.findall(question):
        if not _phrase_matches(outside_names, token):
            continue
        if token in known:
            resolution = resolve_company(token, aliases)
            if resolution.status is CompanyResolutionStatus.RESOLVED:
                company = resolution.company
                found.setdefault(company.ticker, CompanyMention(company.ticker, company.name, token))
        elif token not in _NON_TICKER_TOKENS and token not in unknown:
            unknown.append(token)
    return list(found.values()), unknown


_TERM_PHRASES: tuple[str, ...] | None = None


def _term_phrases() -> tuple[str, ...]:
    global _TERM_PHRASES
    if _TERM_PHRASES is None:
        phrases = {canonical_phrasing(fold(phrase)) for metric in METRICS for phrase in metric.synonyms}
        phrases |= {canonical_phrasing(fold(phrase)) for ratio in RATIOS for phrase in ratio.synonyms}
        _TERM_PHRASES = tuple(sorted(phrase for phrase in phrases if " " in phrase))
    return _TERM_PHRASES


def _abbreviations(question: str, unknown: list[str]) -> list[str]:
    """Unknown upper-case tokens that abbreviate the term printed right before them ("thu nhập lãi thuần (NII)").

    Only a parenthesis that directly follows a metric or ratio name counts; a
    company name before "(XYZ)" never makes XYZ an abbreviation.
    """
    found = []
    for token in unknown:
        uses = list(re.finditer(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])", question))

        def abbreviates(use: re.Match) -> bool:
            before = re.search(r"\(\s*$", question[:use.start()])
            if before is None or not re.match(r"\s*\)", question[use.end():]):
                return False
            prefix = canonical_phrasing(fold(question[:before.start()])).rstrip()
            return any(prefix.endswith(phrase) and (len(prefix) == len(phrase) or not prefix[-len(phrase) - 1].isalnum())
                       for phrase in _term_phrases())

        if uses and all(abbreviates(use) for use in uses):
            found.append(token)
    return found


def understand_question(question: str, catalog: DatasetCatalog) -> T2PUnderstanding:
    folded = fold(question)
    companies, unknown = _companies(question, catalog)
    abbreviations = _abbreviations(question, unknown)
    unknown = [token for token in unknown if token not in abbreviations]
    terms = canonical_phrasing(folded)
    metrics, ratios, ratio_spans = _detect_terms(terms)
    # Words inside a ratio's own name ("... vốn chủ sở hữu bình quân") are not operation cues.
    cues = terms
    for start, end in ratio_spans:
        cues = cues[:start] + " " * (end - start) + cues[end:]
    periods = parse_periods(TemporalParserInput(question))
    sub_annual = any(
        resolution.period is not None and resolution.period.kind is not PeriodKind.NAM
        for resolution in periods.resolutions
    )
    scope = resolve_statement_scope(StatementScopeResolverInput(question, None))
    scope_value = None
    scope_inferred = False
    if scope.status is StatementScopeResolutionStatus.RESOLVED and scope.statement_scope is not None:
        scope_value = scope.statement_scope.value.value if hasattr(scope.statement_scope.value, "value") \
            else scope.statement_scope.value
        scope_inferred = bool(getattr(scope.statement_scope, "inferred", False))
    operation = detect_operation(OperationDetectorInput(question)).operation.value
    if any(cue in cues for cue in ("tang truong", "growth", "tang bao nhieu %")):
        operation = "growth"
    elif any(cue in cues for cue in ("trung binh", "binh quan", "average", "mean", "tong lnst", "sum")):
        operation = "aggregate"
    requested = parse_requested_scale_unit(RequestedScaleUnitParserInput(question))
    return T2PUnderstanding(
        question=question,
        companies=tuple(companies),
        unknown_tickers=tuple(unknown),
        years=tuple(_years(question)),
        all_periods=any(cue in folded for cue in _ALL_PERIOD_CUES),
        sub_annual_period=sub_annual,
        statement_scope=scope_value,
        scope_inferred=scope_inferred,
        metrics=tuple(metrics),
        ratios=tuple(ratios),
        operation=operation,
        ranking=any(re.search(r"(?<![a-z])" + re.escape(cue) + r"(?![a-z])", folded) for cue in _RANKING_CUES),
        growth=any(cue in cues for cue in _GROWTH_CUES),
        average=any(cue in cues for cue in _AVERAGE_CUES),
        requested_scale=None if requested.requested_scale is None else requested.requested_scale.value,
        unavailable=tuple(dict.fromkeys(reason for phrase, reason in _UNAVAILABLE if phrase in folded)),
    )


@dataclass(frozen=True)
class Discovery:
    decision: GateDecision
    reason: str | None
    message: str | None
    data: DatasetSlice | None = None
    context: dict[str, Any] | None = None
    expected_datasets: tuple[str, ...] = field(default_factory=tuple)
    mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "message": self.message,
            "mode": self.mode,
            "expected_datasets": list(self.expected_datasets),
            "rows": None if self.data is None else self.data.row_counts(),
        }


def _stop(decision: GateDecision, reason: str, message: str) -> Discovery:
    return Discovery(decision, reason, message)


def discover(understanding: T2PUnderstanding, catalog: DatasetCatalog) -> Discovery:
    """Gate the question and select the rows and schema the generator may use."""
    if understanding.unknown_tickers:
        return _stop(GateDecision.NO_DATA, "UNKNOWN_COMPANY",
                     f"Không có dữ liệu cho mã/công ty: {', '.join(understanding.unknown_tickers)}.")
    if understanding.unavailable:
        return _stop(GateDecision.UNSUPPORTED, "UNAVAILABLE_DATA",
                     "Bộ dữ liệu chỉ gồm báo cáo tài chính, không có: " + "; ".join(understanding.unavailable) + ".")
    if understanding.sub_annual_period:
        return _stop(GateDecision.UNSUPPORTED, "SUB_ANNUAL_PERIOD",
                     "Bộ dữ liệu chỉ có số liệu năm; không có số liệu quý/lũy kế.")
    tickers = understanding.tickers
    if not tickers and not understanding.required_metrics:
        return _stop(GateDecision.NEEDS_CLARIFICATION, "MISSING_COMPANY_OR_METRIC",
                     "Vui lòng nêu công ty hoặc chỉ tiêu tài chính cần tra cứu.")
    if not understanding.years and not understanding.all_periods:
        return _stop(GateDecision.NEEDS_CLARIFICATION, "MISSING_PERIOD", "Vui lòng nêu năm tài chính cần tra cứu.")

    if tickers:
        coverage = catalog.coverage(tickers)
        years = understanding.years or sorted({year for (_, year) in coverage})
        covered = [(ticker, year) for ticker in tickers for year in years if (ticker, year) in coverage]
        if understanding.years and not covered:
            return _stop(GateDecision.NO_DATA, "PERIOD_OUTSIDE_COVERAGE",
                         f"Không có báo cáo của {', '.join(tickers)} cho năm {', '.join(map(str, years))}.")
        if understanding.statement_scope is None:
            return _stop(GateDecision.NEEDS_CLARIFICATION, "MISSING_SCOPE",
                         "Vui lòng nêu loại báo cáo (hợp nhất hay riêng).")
        data = catalog.slice(tickers=tickers)
        mode = "COMPANY"
    else:
        if understanding.statement_scope is None:
            return _stop(GateDecision.NEEDS_CLARIFICATION, "MISSING_SCOPE",
                         "Vui lòng nêu loại báo cáo (hợp nhất hay riêng) để so sánh giữa các công ty.")
        data = catalog.slice(metrics=understanding.required_metrics)
        mode = "SCREENING"
    expected = tuple(dict.fromkeys(
        dataset for dataset in (metric_dataset(metric) for metric in understanding.required_metrics) if dataset
    ))
    context = schema_context(
        data,
        relevant_metrics=understanding.required_metrics,
        include_line_items=mode == "COMPANY" and not understanding.required_metrics,
    )
    return Discovery(GateDecision.PROCEED, None, None, data, context, expected, mode)
