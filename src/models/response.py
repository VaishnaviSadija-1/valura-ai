from typing import List, Optional
from pydantic import BaseModel


class SafetyResult(BaseModel):
    blocked: bool
    category: Optional[str] = None
    response: Optional[str] = None


class EntitySet(BaseModel):
    tickers: List[str] = []
    amounts: List[float] = []
    time_period: Optional[str] = None
    sectors: List[str] = []
    topics: List[str] = []


class SafetyVerdict(BaseModel):
    flag: bool = False
    reason: Optional[str] = None


class ClassificationResult(BaseModel):
    intent: str
    entities: EntitySet
    target_agent: str
    safety_verdict: SafetyVerdict
    follow_up_resolved: bool = False


class ConcentrationRisk(BaseModel):
    top_position_pct: Optional[float] = None
    top_3_positions_pct: Optional[float] = None
    flag: Optional[str] = None  # high, medium, low
    status: Optional[str] = None  # not_applicable
    reason: Optional[str] = None


class Performance(BaseModel):
    total_return_pct: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    measurement_period: Optional[str] = None
    return_basis: Optional[str] = None  # actual, estimated
    status: Optional[str] = None
    reason: Optional[str] = None


class BenchmarkComparison(BaseModel):
    benchmark: Optional[str] = None
    benchmark_ticker: Optional[str] = None
    portfolio_return_pct: Optional[float] = None
    benchmark_return_pct: Optional[float] = None
    alpha_pct: Optional[float] = None
    status: Optional[str] = None
    reason: Optional[str] = None


class Observation(BaseModel):
    severity: str  # warning, info, ok
    text: str


class PortfolioHealthResult(BaseModel):
    mode: str  # monitoring, onboarding
    concentration_risk: ConcentrationRisk
    performance: Performance
    benchmark_comparison: BenchmarkComparison
    observations: List[Observation]
    next_steps: List[str]
    disclaimer: str
    session_id: str


class StubResult(BaseModel):
    status: str = "not_implemented"
    intent: str
    entities: EntitySet
    agent: str
    message: str


class SSEEvent(BaseModel):
    event: str
    data: dict
