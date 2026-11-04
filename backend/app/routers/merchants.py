from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.db import get_session
from app.models import Merchant, Product

router = APIRouter(tags=["merchants"])


@router.get("/merchants")
def list_merchants(session: Session = Depends(get_session)) -> list[Merchant]:
    return list(session.exec(select(Merchant).order_by(Merchant.id)).all())  # type: ignore[arg-type]


@router.get("/products")
def list_products(session: Session = Depends(get_session)) -> list[Product]:
    return list(session.exec(select(Product).order_by(Product.id)).all())  # type: ignore[arg-type]
