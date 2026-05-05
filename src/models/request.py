from typing import List, Optional
from pydantic import BaseModel, Field


class Holding(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10, pattern=r'^[A-Z0-9\.\-\^=]+$')
    quantity: float = Field(..., gt=0)
    current_price: float = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    cost_basis: Optional[float] = None
    purchase_date: Optional[str] = None  # ISO 8601


class Portfolio(BaseModel):
    holdings: List[Holding] = []
    base_currency: str = "USD"


class UserContext(BaseModel):
    portfolio: Portfolio = Portfolio()
    kyc_status: str = "none"  # verified, pending, none
    risk_profile: str = "moderate"  # conservative, moderate, aggressive


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    user_context: UserContext = UserContext()
