from typing import Optional

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Merchant, Product

MERCHANTS = [
    ("Chai Corner", "food"),
    ("Taco Truck", "food"),
    ("Rickshaw Rides", "transport"),
    ("Craft Beer Tent", "alcohol"),
    ("Gadget Stall", "electronics"),
]

# (name, price, category, merchant name)
PRODUCTS = [
    ("Masala Chai", 2.50, "food", "Chai Corner"),
    ("Samosa", 3.00, "food", "Chai Corner"),
    ("Potato Chips", 2.50, "food", "Chai Corner"),
    ("Cookies", 3.50, "food", "Chai Corner"),
    ("Chicken Taco", 4.00, "food", "Taco Truck"),
    ("Nachos", 5.50, "food", "Taco Truck"),
    ("Popcorn", 3.00, "food", "Taco Truck"),
    ("City Ride", 6.00, "transport", "Rickshaw Rides"),
    ("Airport Ride", 15.00, "transport", "Rickshaw Rides"),
    ("IPA Pint", 6.00, "alcohol", "Craft Beer Tent"),
    ("Phone Charger", 12.00, "electronics", "Gadget Stall"),
    ("Earbuds", 25.00, "electronics", "Gadget Stall"),
]


def seed_if_empty(session: Session, payout_email: Optional[str] = None) -> None:
    email = payout_email if payout_email is not None else get_settings().merchant_payout_email
    if not session.exec(select(Merchant)).first():
        for name, category in MERCHANTS:
            session.add(Merchant(name=name, category=category, paypal_email=email))
        session.commit()
    elif email:
        # Keep demo payout emails in step with .env (merchants may have been seeded earlier).
        for m in session.exec(select(Merchant)).all():
            if m.paypal_email != email:
                m.paypal_email = email
                session.add(m)
        session.commit()
    if not session.exec(select(Product)).first():
        ids = {m.name: m.id for m in session.exec(select(Merchant)).all()}
        for name, price, category, merchant in PRODUCTS:
            if merchant in ids:
                session.add(Product(name=name, price=price, category=category, merchant_id=ids[merchant]))
        session.commit()
