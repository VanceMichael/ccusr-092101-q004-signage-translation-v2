"""HTTP 接口的请求/响应模型。时间字段在服务层做带偏移量校验。"""

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 发布包
class BasisDoc(BaseModel):
    doc_ref: str
    title: str = ""
    sha256: str = ""


class Entry(BaseModel):
    term: str
    translation: str
    note: str = ""


class ExceptionNote(BaseModel):
    term_key: str
    scope: str = ""           # 适用范围，如“三坊七巷历史建筑群”
    reason: str = ""


class PackageIssueRequest(BaseModel):
    category: str = Field(..., description="场所类别，如 airport/hospital/scenic/road/gov_web")
    language_code: str
    effective_at: str         # ISO 8601 带偏移量，可为未来时间
    issued_by: str
    basis_docs: list[BasisDoc] = []
    entries: dict[str, Entry] # term_key -> 标准词条
    exceptions: list[ExceptionNote] = []


# ---------------------------------------------------------------- 单位 / 订阅
class UnitRegisterRequest(BaseModel):
    unit_id: str
    name: str
    managed_categories: list[str] = []
    managed_locations: list[str] = []


class SubscribeRequest(BaseModel):
    categories: list[str]


# ---------------------------------------------------------------- 标识目录上报
class SignItem(BaseModel):
    sign_ref: str
    location_code: str
    category: str
    language_code: str
    term_key: str
    package_id: str           # 该标识声明合法引用的发布包
    observed_text: str


class CatalogReport(BaseModel):
    batch_id: str = Field(..., description="离线批次号；同号重放返回同一回执")
    items: list[SignItem]


# ---------------------------------------------------------------- 候选决策 / 承诺
class CandidateDecisionRequest(BaseModel):
    action: Literal["confirm", "reject"]
    reason: str = ""
    promised_by: str | None = None   # confirm 时单位承诺期限（ISO 8601 带偏移量）


# ---------------------------------------------------------------- 豁免
class ExemptionRequest(BaseModel):
    reason: str
    valid_until: str          # 申请的豁免截止时间
    kind: Literal["construction", "historic_building", "other"] = "other"


class ExemptionRevokeRequest(BaseModel):
    reason: str = ""


class PromiseRequest(BaseModel):
    promised_by: str


class ExemptionApproveRequest(BaseModel):
    valid_until: str
    reason: str = ""
