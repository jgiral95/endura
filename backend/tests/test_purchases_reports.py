from decimal import Decimal
from datetime import date


def D(v):
    return Decimal(str(v))


def test_purchase_landed_cost_and_receive(admin, make_product):
    a = make_product(qty=0, cost=0)
    b = make_product(qty=0, cost=0)
    r = admin.post("/purchases", json={"purchase_date": date.today().isoformat(), "supplier": "Maurten", "funded_by": "DEISY",
                                       "currency": "USD", "fx_rate": 4000, "shipping_cost": 100000, "other_costs": 0,
                                       "items": [{"product_id": a["id"], "quantity": 10, "unit_price": 3, "expiry_date": "2027-05-01"},
                                                 {"product_id": b["id"], "quantity": 10, "unit_price": 2}]})
    assert r.status_code == 201, r.text
    pu = r.json()
    assert D(pu["goods_total"]) == 200000 and D(pu["total_cop"]) == 300000
    assert admin.get(f"/inventory/{a['id']}").json()["quantity"] == 0
    item_b = [i for i in pu["items"] if i["product_id"] == b["id"]][0]
    r = admin.post(f"/purchases/{pu['id']}/receive", json={"items": [{"id": item_b["id"], "expiry_date": "2027-09-01"}]})
    assert r.status_code == 200, r.text
    # Envío 100.000 repartido por valor: A (120.000) recibe 60.000 → 18.000 por unidad; B (80.000) recibe 40.000 → 12.000
    pa = admin.get(f"/inventory/{a['id']}").json()
    pb = admin.get(f"/inventory/{b['id']}").json()
    assert pa["quantity"] == 10 and D(pa["unit_cost"]) == 18000 and pa["next_expiry"] == "2027-05-01"
    assert D(pb["unit_cost"]) == 12000 and pb["next_expiry"] == "2027-09-01"
    assert admin.post(f"/purchases/{pu['id']}/receive").status_code == 409
    # Reversión permitida porque nada se movió
    assert admin.post(f"/purchases/{pu['id']}/unreceive").status_code == 200
    assert admin.get(f"/inventory/{a['id']}").json()["quantity"] == 0


def test_unreceive_blocked_after_sale(admin, make_product):
    a = make_product(qty=0, cost=0)
    pu = admin.post("/purchases", json={"purchase_date": date.today().isoformat(), "supplier": "GU",
                                        "items": [{"product_id": a["id"], "quantity": 2, "unit_price": 5000}]}).json()
    admin.post(f"/purchases/{pu['id']}/receive")
    admin.post("/sales", json={"items": [{"product_id": a["id"], "quantity": 1, "unit_price": 9000}]})
    assert admin.post(f"/purchases/{pu['id']}/unreceive").status_code == 409


def test_monthly_result(admin, make_product):
    month = date.today().strftime("%Y-%m")
    before = admin.get("/reports/summary", params={"month": month}).json()["figures"]
    p = make_product(qty=10, cost=5000, price=10000)
    admin.post("/sales", json={"items": [{"product_id": p["id"], "quantity": 2, "unit_price": 10000}], "delivery_fee": 5000})
    admin.post("/sales", json={"kind": "giveaway", "customer_name": "Evento", "reason": "event",
                               "items": [{"product_id": p["id"], "quantity": 1, "unit_price": 10000}]})
    admin.post("/expenses", json={"expense_date": date.today().isoformat(), "category": "delivery", "description": "Domicilio", "amount": 6000})
    after = admin.get("/reports/summary", params={"month": month}).json()["figures"]
    delta = D(after["result"]) - D(before["result"])
    # 20.000 ingreso − 10.000 coste + 5.000 domicilio − 5.000 patrocinio − 6.000 gasto
    assert delta == 4000


def test_expense_void_admin_only(admin, user):
    e = user.post("/expenses", json={"expense_date": date.today().isoformat(), "category": "marketing",
                                     "description": "Pendón", "amount": 50000}).json()
    assert user.post(f"/expenses/{e['id']}/void", json={"reason": "error"}).status_code == 403
    assert admin.post(f"/expenses/{e['id']}/void", json={"reason": "error"}).status_code == 200


def test_partners_share(admin):
    import os
    name = f"Socio-{os.getpid()}"
    p = admin.post("/partners", json={"name": name}).json()
    admin.post("/capital", json={"partner_id": p["id"], "movement_date": date.today().isoformat(), "kind": "cash", "amount": 1000000})
    d = admin.get("/partners").json()
    row = [x for x in d["partners"] if x["id"] == p["id"]][0]
    assert D(row["cash"]) == 1000000 and D(row["share_pct"]) > 0
