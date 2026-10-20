from decimal import Decimal


def test_lots_weighted_cost_and_fefo_exit(admin, make_product):
    p = make_product(qty=10, cost=10000, expiry="2027-06-01")
    r = admin.post(f"/inventory/{p['id']}/entry", json={"quantity": 10, "unit_cost": 12000, "expiry_date": "2027-01-01"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["quantity"] == 20 and Decimal(str(d["unit_cost"])) == Decimal("11000.00")
    assert d["next_expiry"] == "2027-01-01"
    # La salida toma primero el lote que vence antes (enero 2027, coste 12.000)
    r = admin.post(f"/inventory/{p['id']}/exit", json={"quantity": 4, "note": "Muestra dañada"})
    d = r.json()
    lots = {l["expiry_date"]: l["quantity"] for l in d["lots"]}
    assert lots == {"2027-01-01": 6, "2027-06-01": 10}
    mv = admin.get("/movements", params={"product_id": p["id"]}).json()["items"]
    assert mv[0]["movement_type"] == "manual_out" and mv[0]["quantity_delta"] == -4 and Decimal(str(mv[0]["unit_cost"])) == 12000


def test_cannot_exit_more_than_stock(admin, make_product):
    p = make_product(qty=2)
    r = admin.post(f"/inventory/{p['id']}/exit", json={"quantity": 3, "note": "prueba"})
    assert r.status_code == 409


def test_expiring_filter(admin, make_product):
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=20)).isoformat()
    p = make_product(qty=5, expiry=soon)
    items = admin.get("/inventory", params={"status": "expiring"}).json()["items"]
    assert any(i["id"] == p["id"] and i["expiring_qty"] == 5 for i in items)


def test_user_cannot_manage_brands(user):
    assert user.post("/brands", json={"name": "No permitido"}).status_code == 403


def test_deactivate_requires_zero_stock(admin, make_product):
    p = make_product(qty=1)
    assert admin.delete(f"/inventory/{p['id']}").status_code == 409
    p2 = make_product(qty=0)
    assert admin.delete(f"/inventory/{p2['id']}").status_code == 200
