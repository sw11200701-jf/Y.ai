from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.contracts import FactQuery, FinancialFact, BusinessContext
from app.db import get_session
from app.context import get_context
from .domain import METRICS, VERSION
from .service import query_facts, calculate_dataset

router = APIRouter(tags=["finance"])

class CalculateRequest(BaseModel):
    facts: list[FinancialFact]
    query: FactQuery

@router.get("/metrics")
def metrics():
    return {"items": [{"metric_code": code, "label": label, "version": VERSION} for code, label in METRICS.items()],
            "formula_policy": {"yoy": "(current-prior)/abs(prior)", "net_profit_margin": "net_profit/revenue",
                               "debt_to_assets": "total_liabilities/total_assets", "unit": "yuan"}}

@router.post("/facts/query")
async def facts_query(query: FactQuery, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    return {"items": await query_facts(query=query, session=session, context=context)}

@router.post("/datasets/calculate")
async def calculate(body: CalculateRequest, session: Session = Depends(get_session), context: BusinessContext = Depends(get_context)):
    result = await calculate_dataset(facts=body.facts, query=body.query, session=session, context=context)
    session.commit()
    return result
