from sqlmodel import Session, select

from app.models import Merchant, Product
from app.seed import PRODUCTS, seed_if_empty
from tests.fakes import make_engine


def products_by_merchant(s: Session) -> dict[str, list[str]]:
    names = {m.id: m.name for m in s.exec(select(Merchant)).all()}
    out: dict[str, list[str]] = {}
    for p in s.exec(select(Product)).all():
        out.setdefault(names[p.merchant_id], []).append(p.name)
    return out


def test_seed_has_beer_at_beer_tent_and_gadgets_at_gadget_stall(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as s:
        seed_if_empty(s, "m@test.com")
        by = products_by_merchant(s)
        assert {"IPA Pint", "Craft IPA 6-Pack", "Lager 6-Pack"} <= set(by["Craft Beer Tent"])
        assert {"Phone Charger", "Earbuds"} <= set(by["Gadget Stall"])
        cats = {p.name: p.category for p in s.exec(select(Product)).all()}
        assert cats["Craft IPA 6-Pack"] == "alcohol" and cats["Earbuds"] == "electronics"


def test_seed_is_idempotent_and_tops_up_an_older_database(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as s:
        seed_if_empty(s, "m@test.com")
        seed_if_empty(s, "m@test.com")
        assert len(s.exec(select(Product)).all()) == len(PRODUCTS)
        old = s.exec(select(Product).where(Product.name == "Craft IPA 6-Pack")).one()
        s.delete(old)  # simulate a DB seeded before this product existed
        s.commit()
        seed_if_empty(s, "m@test.com")
        assert len(s.exec(select(Product)).all()) == len(PRODUCTS)
