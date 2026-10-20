from decimal import Decimal
import uuid


def D(v):
    return Decimal(str(v))


def test_sale_discount_delivery_cost_and_payments(admin, make_product):
    p = make_product(qty=10, cost=7500, price=10000, expiry="2027-04-01")
    r = admin.post("/sales", json={"customer_name": "Andrés", "referrer": "JARDÍN",
                                   "items": [{"product_id": p["id"], "quantity": 4, "unit_price": 10000}],
                                   "discount_percent": 10, "delivery_fee": 8000,
                                   "payment": {"amount": 20000, "method": "bancolombia"}})
    assert r.status_code == 201, r.text
    s = r.json()
    assert D(s["subtotal"]) == 40000 and D(s["discount"]) == 4000 and D(s["total"]) == 44000
    assert D(s["cost_total"]) == 30000 and D(s["margin"]) == 6000
    assert s["payment_status"] == "partial" and D(s["balance"]) == 24000
    assert admin.get(f"/inventory/{p['id']}").json()["quantity"] == 6
    # Abono que supera el saldo: rechazado
    assert admin.post(f"/sales/{s['id']}/payments", json={"amount": 30000, "method": "cash"}).status_code == 422
    s2 = admin.post(f"/sales/{s['id']}/payments", json={"amount": 24000, "method": "cash"}).json()
    assert s2["payment_status"] == "paid"
    rec = admin.get("/receivables").json()
    assert all(x["id"] != s["id"] for x in rec["items"])


def test_sale_numbers_are_consecutive_and_idempotent(admin, make_product):
    p = make_product(qty=5)
    ref = str(uuid.uuid4())
    body = {"items": [{"product_id": p["id"], "quantity": 1, "unit_price": 15000}], "client_ref": ref}
    a = admin.post("/sales", json=body)
    b = admin.post("/sales", json=body)
    assert a.status_code == 201 and b.status_code == 200 and b.json()["duplicate"]
    c = admin.post("/sales", json={"items": [{"product_id": p["id"], "quantity": 1, "unit_price": 15000}]}).json()
    assert c["number"] == a.json()["number"] + 1
    assert admin.get(f"/inventory/{p['id']}").json()["quantity"] == 3


def test_insufficient_stock(admin, make_product):
    p = make_product(qty=1)
    r = admin.post("/sales", json={"items": [{"product_id": p["id"], "quantity": 2, "unit_price": 1}]})
    assert r.status_code == 409


def test_void_returns_units_to_original_lots(admin, user, make_product):
    p = make_product(qty=3, cost=5000, expiry="2027-01-01")
    admin.post(f"/inventory/{p['id']}/entry", json={"quantity": 3, "unit_cost": 6000, "expiry_date": "2028-01-01"})
    s = admin.post("/sales", json={"items": [{"product_id": p["id"], "quantity": 4, "unit_price": 9000}]}).json()
    assert D(s["cost_total"]) == 3 * 5000 + 6000
    assert user.post(f"/sales/{s['id']}/void", json={"reason": "Prueba"}).status_code == 403
    r = admin.post(f"/sales/{s['id']}/void", json={"reason": "Cliente canceló"})
    assert r.status_code == 200
    lots = {l["expiry_date"]: l["quantity"] for l in admin.get(f"/inventory/{p['id']}").json()["lots"]}
    assert lots == {"2027-01-01": 3, "2028-01-01": 3}
    assert admin.post(f"/sales/{s['id']}/void", json={"reason": "otra vez"}).status_code == 409


def test_giveaway_has_no_revenue_but_records_cost(admin, make_product):
    p = make_product(qty=5, cost=8000, price=12000)
    r = admin.post("/sales", json={"kind": "giveaway", "customer_name": "Iván", "reason": "ambassador",
                                   "items": [{"product_id": p["id"], "quantity": 2, "unit_price": 12000}]})
    assert r.status_code == 201, r.text
    g = r.json()
    assert D(g["total"]) == 0 and D(g["subtotal"]) == 24000 and D(g["cost_total"]) == 16000 and g["payment_status"] == "n/a"
    assert admin.post("/sales", json={"kind": "giveaway", "customer_name": "X",
                                      "items": [{"product_id": p["id"], "quantity": 1, "unit_price": 1}]}).status_code == 422


def test_future_date_rejected(admin, make_product):
    p = make_product(qty=1)
    r = admin.post("/sales", json={"sale_date": "2999-01-01", "items": [{"product_id": p["id"], "quantity": 1, "unit_price": 1}]})
    assert r.status_code == 422
