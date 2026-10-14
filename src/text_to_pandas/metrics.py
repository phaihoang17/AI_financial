"""Canonical statement-line metric dictionary for the Text-to-Pandas datasets.

A metric is a named VAS statement line. The catalog builder assigns a metric to
a row only when its TT200 line code (``Mã số``) is listed here *and* its label
matches ``label_pattern`` (an OCR-misread code alone never assigns a metric).
Code-less bank-template (TT49) rows use ``bank_label_pattern`` on the whole
label. Derived ratios live in ``src/formulas/ratios.py``; this module holds no
arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from src.understanding.metric_normalizer import initial_metric_registry

INCOME_STATEMENT = "INCOME_STATEMENT"
BALANCE_SHEET = "BALANCE_SHEET"
CASH_FLOW_STATEMENT = "CASH_FLOW_STATEMENT"
STATEMENTS = (INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW_STATEMENT)
DATASET_BY_STATEMENT = {
    INCOME_STATEMENT: "income_statement",
    BALANCE_SHEET: "balance_sheet",
    CASH_FLOW_STATEMENT: "cash_flow",
}


def fold(text: str) -> str:
    """Casefolded, diacritic-free, single-spaced text for tolerant OCR matching."""
    text = unicodedata.normalize("NFKC", text).replace("đ", "d").replace("Đ", "D")
    text = "".join(
        char for char in unicodedata.normalize("NFD", text) if not unicodedata.combining(char)
    )
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    statement: str
    vas_codes: tuple[str, ...]
    label_pattern: str
    bank_label_pattern: str | None
    name_vi: str
    unit: str
    description: str
    synonyms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "statement": self.statement,
            "dataset": DATASET_BY_STATEMENT[self.statement],
            "vas_codes": list(self.vas_codes),
            "name_vi": self.name_vi,
            "unit": self.unit,
            "description": self.description,
        }


def _m(metric_id, statement, codes, label, name_vi, description, synonyms=(), *,
       bank=None, unit="VND") -> MetricDefinition:
    return MetricDefinition(metric_id, statement, tuple(codes), label, bank, name_vi,
                            unit, description, tuple(synonyms))


_LNST_SYNONYMS = tuple(item.synonym for item in initial_metric_registry())
# "Lưu chuyển tiền thuần từ", "… sử dụng vào", "… từ/(sử dụng vào)" before the activity.
_NET_FLOW = r"luu chuyen tien thuan[^a-z]*(?:(?:tu|su dung vao|dung vao)[^a-z]*)*"

METRICS: tuple[MetricDefinition, ...] = (
    # Income statement (TT200 B02-DN).
    _m("GROSS_REVENUE", INCOME_STATEMENT, ["01"], r"doanh thu ban hang", "Doanh thu bán hàng và cung cấp dịch vụ",
       "Gross sales and service revenue before deductions (line 01).",
       ["doanh thu bán hàng và cung cấp dịch vụ", "doanh thu gộp", "gross revenue"]),
    _m("NET_REVENUE", INCOME_STATEMENT, ["10"], r"doanh thu thuan", "Doanh thu thuần",
       "Net sales and service revenue after deductions (line 10).",
       ["doanh thu thuần", "doanh thu", "net revenue", "revenue", "net sales"]),
    _m("COGS", INCOME_STATEMENT, ["11"], r"gia von", "Giá vốn hàng bán",
       "Cost of goods sold (line 11).", ["giá vốn hàng bán", "giá vốn", "cogs", "cost of goods sold"]),
    _m("GROSS_PROFIT", INCOME_STATEMENT, ["20"], r"loi nhuan gop", "Lợi nhuận gộp",
       "Gross profit (line 20).", ["lợi nhuận gộp", "lãi gộp", "gross profit"]),
    _m("FINANCIAL_INCOME", INCOME_STATEMENT, ["21"], r"doanh thu hoat dong tai chinh", "Doanh thu hoạt động tài chính",
       "Financial income (line 21).", ["doanh thu hoạt động tài chính", "doanh thu tài chính", "financial income"]),
    _m("FINANCIAL_EXPENSES", INCOME_STATEMENT, ["22"], r"chi phi tai chinh", "Chi phí tài chính",
       "Financial expenses (line 22).", ["chi phí tài chính", "financial expenses"]),
    _m("INTEREST_EXPENSE", INCOME_STATEMENT, ["23"], r"lai vay", "Chi phí lãi vay",
       "Interest expense (line 23).", ["chi phí lãi vay", "lãi vay", "interest expense"]),
    _m("SELLING_EXPENSES", INCOME_STATEMENT, ["24", "25"], r"chi phi ban hang", "Chi phí bán hàng",
       "Selling expenses (line 25; 24 in older forms).", ["chi phí bán hàng", "selling expenses"]),
    _m("GA_EXPENSES", INCOME_STATEMENT, ["25", "26"], r"chi phi quan ly doanh nghiep", "Chi phí quản lý doanh nghiệp",
       "General and administrative expenses (line 26; 25 in older forms).",
       ["chi phí quản lý doanh nghiệp", "chi phí quản lý", "g&a"]),
    _m("OPERATING_PROFIT", INCOME_STATEMENT, ["30"], r"loi nhuan thuan tu hoat dong kinh doanh",
       "Lợi nhuận thuần từ hoạt động kinh doanh", "Operating profit (line 30).",
       ["lợi nhuận thuần từ hoạt động kinh doanh", "lợi nhuận từ hoạt động kinh doanh", "operating profit"]),
    _m("OTHER_INCOME", INCOME_STATEMENT, ["31"], r"thu nhap khac", "Thu nhập khác", "Other income (line 31).",
       ["thu nhập khác", "other income"]),
    _m("OTHER_EXPENSES", INCOME_STATEMENT, ["32"], r"chi phi khac", "Chi phí khác", "Other expenses (line 32).",
       ["chi phí khác", "other expenses"]),
    _m("OTHER_PROFIT", INCOME_STATEMENT, ["40"], r"loi nhuan khac", "Lợi nhuận khác", "Other profit (line 40).",
       ["lợi nhuận khác", "other profit"]),
    _m("PROFIT_BEFORE_TAX", INCOME_STATEMENT, ["50"], r"truoc thue", "Tổng lợi nhuận kế toán trước thuế",
       "Accounting profit before tax (line 50).",
       ["lợi nhuận trước thuế", "tổng lợi nhuận kế toán trước thuế", "lntt", "profit before tax", "pbt"],
       bank=r"(?:tong )?loi nhuan (?:ke toan )?truoc thue"),
    _m("CURRENT_TAX", INCOME_STATEMENT, ["51"], r"hien hanh", "Chi phí thuế TNDN hiện hành",
       "Current corporate income tax expense (line 51).", ["thuế tndn hiện hành", "current income tax"]),
    _m("DEFERRED_TAX", INCOME_STATEMENT, ["52"], r"hoan lai", "Chi phí thuế TNDN hoãn lại",
       "Deferred corporate income tax expense (line 52).", ["thuế tndn hoãn lại", "deferred income tax"]),
    _m("LNST", INCOME_STATEMENT, ["60"], r"loi nhuan sau thue", "Lợi nhuận sau thuế thu nhập doanh nghiệp",
       "Profit after corporate income tax, total (line 60); bank template: the 'Lợi nhuận sau thuế' line.",
       _LNST_SYNONYMS + ("lnst", "net income", "net profit", "profit after tax"),
       bank=r"(?:tong )?loi nhuan sau thue(?: thu nhap doanh nghiep)?"),
    _m("LNST_PARENT", INCOME_STATEMENT, ["61"], r"cong ty me|co dong (?:cua )?cong ty|chu so huu",
       "Lợi nhuận sau thuế của cổ đông công ty mẹ",
       "Profit after tax attributable to owners of the parent (line 61, consolidated statements).",
       ["lợi nhuận sau thuế của cổ đông công ty mẹ", "lợi nhuận sau thuế công ty mẹ", "lnst công ty mẹ",
        "lợi nhuận của cổ đông công ty mẹ"]),
    _m("LNST_MINORITY", INCOME_STATEMENT, ["62"], r"khong kiem soat|thieu so",
       "Lợi nhuận sau thuế của cổ đông không kiểm soát",
       "Profit after tax attributable to non-controlling interests (line 62).",
       ["cổ đông không kiểm soát", "cổ đông thiểu số", "minority interest"]),
    _m("EPS_BASIC", INCOME_STATEMENT, ["70"], r"co ban tren co phieu", "Lãi cơ bản trên cổ phiếu",
       "Basic earnings per share (line 70), VND per share, never scaled by the table unit.",
       ["lãi cơ bản trên cổ phiếu", "eps", "earnings per share"], unit="VND/share"),
    _m("NET_INTEREST_INCOME", INCOME_STATEMENT, [], r"$^", "Thu nhập lãi thuần",
       "Net interest income (bank template only).", ["thu nhập lãi thuần", "net interest income"],
       bank=r"thu nhap lai thuan"),
    # Balance sheet (TT200 B01-DN); values are end-of-fiscal-year balances.
    _m("CURRENT_ASSETS", BALANCE_SHEET, ["100"], r"tai ?san ngan han", "Tài sản ngắn hạn",
       "Total current assets (line 100).", ["tài sản ngắn hạn", "current assets"]),
    _m("CASH_AND_EQUIVALENTS", BALANCE_SHEET, ["110"], r"tien va cac khoan tuong duong tien",
       "Tiền và các khoản tương đương tiền", "Cash and cash equivalents (line 110).",
       ["tiền và các khoản tương đương tiền", "tiền và tương đương tiền", "cash and cash equivalents"]),
    _m("SHORT_TERM_INVESTMENTS", BALANCE_SHEET, ["120"], r"dau tu tai chinh ngan han",
       "Đầu tư tài chính ngắn hạn", "Short-term financial investments (line 120).",
       ["đầu tư tài chính ngắn hạn", "short-term investments"]),
    _m("SHORT_TERM_RECEIVABLES", BALANCE_SHEET, ["130"], r"phai thu ngan han", "Các khoản phải thu ngắn hạn",
       "Short-term receivables (line 130).", ["phải thu ngắn hạn", "short-term receivables"]),
    _m("INVENTORIES", BALANCE_SHEET, ["140"], r"hang ton kho", "Hàng tồn kho",
       "Inventories, net (line 140).", ["hàng tồn kho", "inventories", "inventory"]),
    _m("NON_CURRENT_ASSETS", BALANCE_SHEET, ["200"], r"tai ?san dai han", "Tài sản dài hạn",
       "Total non-current assets (line 200).", ["tài sản dài hạn", "non-current assets"]),
    _m("FIXED_ASSETS", BALANCE_SHEET, ["220"], r"tai san co dinh", "Tài sản cố định",
       "Fixed assets (line 220).", ["tài sản cố định", "fixed assets"]),
    _m("TOTAL_ASSETS", BALANCE_SHEET, ["270"], r"tai ?san", "Tổng cộng tài sản",
       "Total assets (line 270).", ["tổng tài sản", "tổng cộng tài sản", "total assets"],
       bank=r"tong (?:cong )?tai san(?: co)?"),
    _m("LIABILITIES", BALANCE_SHEET, ["300"], r"no phai tra", "Nợ phải trả",
       "Total liabilities (line 300).", ["nợ phải trả", "tổng nợ phải trả", "tổng nợ", "total liabilities",
                                         "liabilities"], bank=r"tong no phai tra"),
    _m("CURRENT_LIABILITIES", BALANCE_SHEET, ["310"], r"no (?:phai tra )?ngan han", "Nợ ngắn hạn",
       "Current liabilities (line 310).", ["nợ ngắn hạn", "nợ phải trả ngắn hạn", "current liabilities"]),
    _m("NON_CURRENT_LIABILITIES", BALANCE_SHEET, ["330"], r"no (?:phai tra )?dai han", "Nợ dài hạn",
       "Non-current liabilities (line 330).", ["nợ dài hạn", "nợ phải trả dài hạn", "non-current liabilities"]),
    _m("EQUITY", BALANCE_SHEET, ["400"], r"von chu so huu", "Vốn chủ sở hữu",
       "Total owners' equity (line 400).", ["vốn chủ sở hữu", "tổng vốn chủ sở hữu", "vcsh", "equity",
                                            "owners' equity"], bank=r"(?:tong )?von chu so huu"),
    _m("CONTRIBUTED_CAPITAL", BALANCE_SHEET, ["411"], r"von gop|von dau tu cua chu so huu",
       "Vốn góp của chủ sở hữu", "Owners' contributed capital (line 411).",
       ["vốn góp của chủ sở hữu", "vốn góp", "vốn điều lệ", "contributed capital"]),
    _m("RETAINED_EARNINGS", BALANCE_SHEET, ["421"], r"chua phan phoi", "Lợi nhuận sau thuế chưa phân phối",
       "Undistributed profit after tax (line 421).",
       ["lợi nhuận sau thuế chưa phân phối", "lợi nhuận chưa phân phối", "retained earnings"]),
    _m("TOTAL_CAPITAL", BALANCE_SHEET, ["440"], r"nguon von|no phai tra va von chu so huu", "Tổng cộng nguồn vốn",
       "Total liabilities and equity (line 440); equals total assets.",
       ["tổng cộng nguồn vốn", "tổng nguồn vốn", "total liabilities and equity"],
       bank=r"tong (?:cong )?(?:nguon von|no phai tra va von chu so huu)"),
    # Cash flow statement (TT200 B03-DN).
    _m("CFO", CASH_FLOW_STATEMENT, ["20"], _NET_FLOW + r"hoat dong kinh doanh",
       "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "Net cash flow from operating activities (line 20).",
       ["lưu chuyển tiền thuần từ hoạt động kinh doanh", "dòng tiền từ hoạt động kinh doanh",
        "dòng tiền thuần từ hoạt động kinh doanh", "dòng tiền hoạt động kinh doanh", "operating cash flow",
        "cfo"]),
    _m("CFI", CASH_FLOW_STATEMENT, ["30"], _NET_FLOW + r"hoat dong dau tu",
       "Lưu chuyển tiền thuần từ hoạt động đầu tư", "Net cash flow from investing activities (line 30).",
       ["lưu chuyển tiền thuần từ hoạt động đầu tư", "dòng tiền từ hoạt động đầu tư",
        "dòng tiền thuần từ hoạt động đầu tư", "investing cash flow"]),
    _m("CFF", CASH_FLOW_STATEMENT, ["40"], _NET_FLOW + r"hoat dong tai chinh",
       "Lưu chuyển tiền thuần từ hoạt động tài chính", "Net cash flow from financing activities (line 40).",
       ["lưu chuyển tiền thuần từ hoạt động tài chính", "dòng tiền từ hoạt động tài chính",
        "dòng tiền thuần từ hoạt động tài chính", "financing cash flow"]),
    _m("NET_CASH_FLOW", CASH_FLOW_STATEMENT, ["50"], r"luu chuyen tien thuan trong", "Lưu chuyển tiền thuần trong năm",
       "Net cash flow for the year (line 50).", ["lưu chuyển tiền thuần trong năm", "net cash flow"]),
    _m("CASH_BEGIN", CASH_FLOW_STATEMENT, ["60"], r"dau (?:nam|ky)", "Tiền và tương đương tiền đầu năm",
       "Cash and cash equivalents at the beginning of the year (line 60).", ["tiền đầu năm", "tiền đầu kỳ"]),
    _m("CASH_END", CASH_FLOW_STATEMENT, ["70"], r"cuoi (?:nam|ky)", "Tiền và tương đương tiền cuối năm",
       "Cash and cash equivalents at the end of the year (line 70).", ["tiền cuối năm", "tiền cuối kỳ"]),
)

METRICS_BY_ID: dict[str, MetricDefinition] = {metric.metric_id: metric for metric in METRICS}
if len(METRICS_BY_ID) != len(METRICS):
    raise RuntimeError("metric IDs must be unique")

_CODE_PATTERNS = {
    metric.metric_id: re.compile(metric.label_pattern) for metric in METRICS
}
_BANK_PATTERNS = {
    metric.metric_id: re.compile(metric.bank_label_pattern)
    for metric in METRICS
    if metric.bank_label_pattern is not None
}
# Leading line ordinals ("1.", "IV.", "a)", "18.1", "L " OCR of "I."), and trailing
# formula parentheticals such as "(60 = 50 - 51 - 52)".
_ORDINAL = re.compile(r"^(?:(?:[0-9]+(?:\.[0-9]+)*|[ivxlc]+|[a-z])\s*[.)\-:]\s*|[0-9]{1,2}\s+|[a-e] - )")
_TRAILING_PAREN = re.compile(r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$")


def label_key(label: str) -> str:
    """Folded label without ordinal or trailing formula/notes parentheticals."""
    text = fold(label)
    previous = None
    while previous != text:
        previous = text
        text = _TRAILING_PAREN.sub("", text).strip(" :.-")
        text = _ORDINAL.sub("", text).strip()
    return text


# Securities companies (Thông tư 334/2016, forms B01/B02-CTCK) share the TT200
# balance-sheet total codes except non-current liabilities (340), and number the
# income-statement profit lines 90/200/201. These codes are tried first; other
# rows fall back to the TT200 codes (older CTCK forms follow TT200 numbering), and
# the agreeing label keeps a reused code from naming a different line.
SECURITIES_CODES: dict[str, dict[str, str]] = {
    BALANCE_SHEET: {"100": "CURRENT_ASSETS", "200": "NON_CURRENT_ASSETS", "270": "TOTAL_ASSETS",
                    "300": "LIABILITIES", "310": "CURRENT_LIABILITIES", "340": "NON_CURRENT_LIABILITIES",
                    "400": "EQUITY", "440": "TOTAL_CAPITAL"},
    INCOME_STATEMENT: {"90": "PROFIT_BEFORE_TAX", "200": "LNST", "201": "LNST_PARENT"},
}
# CTCK forms print line 200 as "Lợi nhuận kế toán sau thuế TNDN"; on TT200 forms
# that wording names the parent's share, so it is accepted only on line 200.
_SECURITIES_PATTERNS = {"LNST": re.compile(r"loi nhuan (?:ke toan )?sau thue")}


def metric_for_code(statement: str, code: str | None, label: str, *, securities: bool = False) -> str | None:
    """The metric of a coded TT200 (or CTCK) row, only when the label agrees with the code."""
    if not code:
        return None
    folded = fold(label)
    if securities:
        metric_id = SECURITIES_CODES.get(statement, {}).get(code)
        if metric_id and _SECURITIES_PATTERNS.get(metric_id, _CODE_PATTERNS[metric_id]).search(folded):
            return metric_id
    matches = [
        metric.metric_id
        for metric in METRICS
        if metric.statement == statement
        and code in metric.vas_codes
        and _CODE_PATTERNS[metric.metric_id].search(folded)
    ]
    return matches[0] if len(matches) == 1 else None


def metric_for_bank_label(statement: str, label: str) -> str | None:
    """The metric of a code-less bank-template row whose whole label names it."""
    key = label_key(label)
    matches = [
        metric_id
        for metric_id, pattern in _BANK_PATTERNS.items()
        if METRICS_BY_ID[metric_id].statement == statement and pattern.fullmatch(key)
    ]
    return matches[0] if len(matches) == 1 else None


def is_per_share_label(label: str) -> bool:
    """Per-share lines (EPS) keep VND/share and are never scaled by the table unit."""
    return "co phieu" in fold(label)
