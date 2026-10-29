"""Fund Desk walkthrough: the whole six-stage flow through the real API, narrated step by step.

    python scripts/walkthrough.py                              # API at http://localhost:8000/api
    python scripts/walkthrough.py --api http://localhost:8020/api --ui http://localhost:5173

Needs a running API on a seeded database (`python -m app.seed`). Standard library only.
It creates real requests on PRJ-001 / PRJ-002, so afterwards you can open them in the web app and
see the same history, ledger and notifications. It signs in four demo users; the login limit is
five per minute, so wait a minute before running it twice in a row.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--api", default="http://localhost:8000/api")
parser.add_argument("--ui", default="http://localhost:5173")
args = parser.parse_args()
API, UI = args.api.rstrip("/"), args.ui.rstrip("/")
TODAY = str(date.today())
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
FAKE_PDF = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64  # a Windows program renamed to .pdf
checks: list[bool] = []


# ----------------------------------------------------------------------------- helpers
def http(method, path, body=None, token=None, headers=None, multipart=None):
    data, ctype = None, None
    if multipart is not None:
        boundary = uuid.uuid4().hex
        name, content = multipart
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/pdf\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        ctype = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data, ctype = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(API + path, method=method, data=data)
    if ctype:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raw = err.read()
        return err.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as err:
        sys.exit(f"Cannot reach the API at {API} ({err.reason}). Start it first, or pass --api.")


def ok(status, body, expected=200):
    if status != expected:
        msg = (body or {}).get("error", {}).get("message", body) if isinstance(body, dict) else body
        sys.exit(f"\n  Unexpected {status} (wanted {expected}): {msg}")
    return body


def rupees(value) -> str:
    n = f"{float(value):,.2f}"
    whole, dec = n.split(".")
    digits = whole.replace(",", "")
    if len(digits) > 3:  # Indian grouping: 12,34,567.00
        head, tail = digits[:-3], digits[-3:]
        head = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1])
        whole = f"{head},{tail}"
    return f"₹{whole}.{dec}"


def stage(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def step(actor, text):
    print(f"  {actor:<12} {text}")


def check(label, condition):
    checks.append(bool(condition))
    print(f"  {'✔' if condition else '✘ FAILED:'} {label}")


def login(email):
    status, body = http("POST", "/auth/login", {"email": email, "password": "Demo@1234"})
    if status == 429:
        sys.exit("Login rate limit reached (5 per minute). Wait a minute and run again.")
    return ok(status, body)["access_token"]


# ----------------------------------------------------------------------------- setup
print("Fund Desk walkthrough")
print(f"API {API}   UI {UI}")
REQ, REQ_B, APPR, FIN = (login(e) for e in
                         ("req.a@demo.local", "req.b@demo.local", "approver.a@demo.local", "finance@demo.local"))
APPROVER_ID = ok(*http("GET", "/me", token=APPR))["id"]
PROJECT = ok(*http("GET", "/projects?q=PRJ-001", token=FIN))["items"][0]
PID = PROJECT["id"]


def detail(rid, token=FIN):
    return ok(*http("GET", f"/requests/{rid}", token=token))


def budget(label):
    p = ok(*http("GET", f"/projects/{PID}", token=FIN))
    print(f"  {'budget':<12} {label}: allocated {rupees(p['allocated'])} · reserved {rupees(p['reserved'])} · "
          f"disbursed {rupees(p['outstanding'])} · liquidated {rupees(p['liquidated'])} · available {rupees(p['available'])}")
    return p


def act(token, path, extra=None, rid=None, headers=None, expected=200):
    body = {"version": detail(rid, token)["version"], **(extra or {})}
    return ok(*http("POST", path, body, token, headers), expected=expected)


def upload(rid, name="receipt.pdf", content=PDF):
    return http("POST", f"/requests/{rid}/documents", token=REQ, multipart=(name, content))


def save_items(rid, items, justification=None):
    body = {"version": detail(rid, REQ)["version"], "items": items}
    if justification is not None:
        body["overspend_justification"] = justification
    return ok(*http("PUT", f"/requests/{rid}/expenses", body, REQ))


# ============================================================================= 1. happy path
stage(f"1. Full flow with corrections on {PROJECT['code']} · {PROJECT['name']}")
before = budget("before")

r = ok(*http("POST", "/requests", {"project_id": PID, "approver_id": APPROVER_ID, "amount_requested": "30000.00",
                                   "purpose": "Medical team travel and stay, camp 5"}, REQ), expected=201)
RID, REF = r["id"], r["ref_no"]
step("Requestor", f"raised {REF} for {rupees(30000)} → {r['status']}")
check("budget reserved immediately", float(budget("after raise")["reserved"]) - float(before["reserved"]) == 30000)

r = act(APPR, f"/requests/{RID}/reject", {"reason": "Split travel and stay into separate lines"}, RID)
step("Approver", f"rejected: \"Split travel and stay into separate lines\" → {r['status']}")
check("reservation released on rejection", r["reserved_amount"] == "0.00")

r = ok(*http("PUT", f"/requests/{RID}", {"version": detail(RID, REQ)["version"], "approver_id": APPROVER_ID,
                                          "amount_requested": "28000.00", "purpose": "Medical team travel (bus) and stay (hostel), camp 5"}, REQ))
step("Requestor", f"corrected and resubmitted for {rupees(28000)} → {r['status']}")
r = act(APPR, f"/requests/{RID}/approve", rid=RID)
step("Approver", f"approved → {r['status']}")

key = str(uuid.uuid4())
pay = {"txn_ref": f"NEFT-{uuid.uuid4().hex[:6].upper()}", "disbursed_on": TODAY, "notes": "Salary account"}
r = act(FIN, f"/requests/{RID}/disburse", pay, RID, {"Idempotency-Key": key})
step("Finance", f"disbursed {rupees(r['disbursed_total'])} ({pay['txn_ref']}) → {r['status']}")
r2 = ok(*http("POST", f"/requests/{RID}/disburse", {"version": r["version"] - 1, **pay}, FIN, {"Idempotency-Key": key}))
check("a retried disburse (same Idempotency-Key) does not pay twice", len(r2["disbursements"]) == 1)
budget("after payout")

stage("2. Expenses with receipts, overspend, item review, verification")
status, body = upload(RID, "invoice.pdf", FAKE_PDF)
check(f"a program renamed to .pdf is rejected by its content ({status})",
      status == 400 and body["error"]["code"] == "invalid_file")
d1 = ok(*upload(RID, "bus-tickets.pdf"), expected=201)
d2 = ok(*upload(RID, "hostel-bill.pdf"), expected=201)
items = [
    {"description": "Bus tickets, 8 people", "amount": "19500.00", "expense_date": TODAY, "category": "Travel", "document_ids": [d1["id"]]},
    {"description": "Hostel, 2 nights", "amount": "10000.00", "expense_date": TODAY, "category": "Lodging", "document_ids": [d2["id"]]},
]
r = save_items(RID, items)
step("Requestor", f"added 2 items with PDF receipts, total {rupees(r['expense_total'])} (requested {rupees(28000)})")
status, body = http("POST", f"/requests/{RID}/expenses/submit", {"version": r["version"]}, REQ)
check(f"submitting above the request without a justification is refused ({status})", status == 422)
r = save_items(RID, [{**it, "id": r["expense_items"][i]["id"]} for i, it in enumerate(items)],
               justification="Hostel rates rose for the festival week")
r = act(REQ, f"/requests/{RID}/expenses/submit", rid=RID)
step("Requestor", f"added a justification and submitted → {r['status']}; extra {rupees(r['overage_reserved'])} reserved")
ITEM1, ITEM2 = (i["id"] for i in r["expense_items"])

act(APPR, f"/requests/{RID}/items/{ITEM1}/review", {"decision": "APPROVED"}, RID)
act(APPR, f"/requests/{RID}/items/{ITEM2}/review", {"decision": "REJECTED", "reason": "Attach the itemised hostel bill"}, RID)
r = act(APPR, f"/requests/{RID}/expenses/send-back", rid=RID)
step("Approver", f"approved item 1, rejected item 2 (\"Attach the itemised hostel bill\") → {r['status']}")
check("approved item 1 is locked for the requestor", next(i for i in r["expense_items"] if i["id"] == ITEM1)["editable"] is False)

d3 = ok(*upload(RID, "hostel-itemised.pdf"), expected=201)
cur = detail(RID, REQ)
r = save_items(RID, [{"id": ITEM2, "description": "Hostel, 2 nights (itemised bill)", "amount": "10000.00",
                      "expense_date": TODAY, "category": "Lodging", "document_ids": [d2["id"], d3["id"]]}],
               justification=cur["overspend_justification"])
r = act(REQ, f"/requests/{RID}/expenses/submit", rid=RID)
step("Requestor", f"attached the itemised bill and resubmitted → {r['status']}")
act(APPR, f"/requests/{RID}/items/{ITEM2}/review", {"decision": "APPROVED"}, RID)
r = act(APPR, f"/requests/{RID}/expenses/approve", rid=RID)
step("Approver", f"approved item 2 and gave final approval → {r['status']}")

act(FIN, f"/requests/{RID}/items/{ITEM1}/verify", {"decision": "VERIFIED"}, RID)
act(FIN, f"/requests/{RID}/items/{ITEM2}/verify", {"decision": "RETURNED", "reason": "GST number missing on the bill"}, RID)
r = act(FIN, f"/requests/{RID}/expenses/return", rid=RID)
step("Finance", f"verified item 1, returned item 2 (\"GST number missing\") → {r['status']}")
check("the returned item must pass approver review again",
      next(i for i in r["expense_items"] if i["id"] == ITEM2)["approval_status"] == "PENDING")

r = act(REQ, f"/requests/{RID}/expenses/submit", rid=RID)
act(APPR, f"/requests/{RID}/items/{ITEM2}/review", {"decision": "APPROVED"}, RID)
r = act(APPR, f"/requests/{RID}/expenses/approve", rid=RID)
step("Req → Appr", f"resubmitted, re-approved → {r['status']}")
act(FIN, f"/requests/{RID}/items/{ITEM2}/verify", {"decision": "VERIFIED"}, RID)
r = act(FIN, f"/requests/{RID}/liquidate", rid=RID)
liq = r["liquidation"]
step("Finance", f"verified and liquidated → {r['status']}: verified {rupees(liq['verified_total'])}, "
                f"disbursed {rupees(liq['disbursed_total'])}, top-up {rupees(liq['top_up_amount'])}")
check("overspend settled as a top-up", liq["top_up_amount"] == "1500.00")
status, _ = http("POST", f"/requests/{RID}/liquidate", {"version": r["version"]}, FIN)
check(f"liquidating the same request twice is refused ({status})", status == 409)
after = budget("after liquidation")
check("project liquidated grew by exactly the verified amount",
      round(float(after["liquidated"]) - float(before["liquidated"]), 2) == 29500.0)

# ============================================================================= 3. underspend
stage("3. Underspend: unused money comes back to the project")
r = ok(*http("POST", "/requests", {"project_id": PID, "approver_id": APPROVER_ID, "amount_requested": "10000.00",
                                   "purpose": "Printing of camp registration forms"}, REQ), expected=201)
RID2, REF2 = r["id"], r["ref_no"]
act(APPR, f"/requests/{RID2}/approve", rid=RID2)
act(FIN, f"/requests/{RID2}/disburse", {**pay, "txn_ref": f"NEFT-{uuid.uuid4().hex[:6].upper()}"}, RID2, {"Idempotency-Key": str(uuid.uuid4())})
d = ok(*http("POST", f"/requests/{RID2}/documents", token=REQ, multipart=("print-bill.pdf", PDF)), expected=201)
r = ok(*http("PUT", f"/requests/{RID2}/expenses", {"version": detail(RID2, REQ)["version"], "items": [
    {"description": "Registration forms, 3,000 copies", "amount": "8200.00", "expense_date": TODAY, "category": "Services",
     "document_ids": [d["id"]]}]}, REQ))
act(REQ, f"/requests/{RID2}/expenses/submit", rid=RID2)
item = detail(RID2, APPR)["expense_items"][0]["id"]
act(APPR, f"/requests/{RID2}/items/{item}/review", {"decision": "APPROVED"}, RID2)
act(APPR, f"/requests/{RID2}/expenses/approve", rid=RID2)
act(FIN, f"/requests/{RID2}/items/{item}/verify", {"decision": "VERIFIED"}, RID2)
step("All roles", f"{REF2}: raised {rupees(10000)}, paid {rupees(10000)}, spent {rupees(8200)}, approved and verified")
status, _ = http("POST", f"/requests/{RID2}/liquidate", {"version": detail(RID2)["version"]}, FIN)
check(f"liquidating an underspend needs a balance-return reference ({status})", status == 422)
r = act(FIN, f"/requests/{RID2}/liquidate", {"balance_return_ref": "UTR-881204"}, RID2)
step("Finance", f"liquidated with balance return {rupees(r['liquidation']['balance_returned'])} (UTR-881204)")
check("unused money recorded as a balance return", r["liquidation"]["balance_returned"] == "1800.00")

# ============================================================================= 4. guard rails
stage("4. Safety checks")
status, _ = http("GET", f"/requests/{RID}", token=REQ_B)
check(f"another requestor cannot see the request ({status})", status == 404)
status, _ = http("POST", "/requests", {"project_id": PID, "approver_id": ok(*http("GET", "/me", token=REQ))["id"],
                                       "amount_requested": "100.00", "purpose": "self approval"}, REQ)
check(f"a requestor cannot approve their own request ({status})", status == 422)
status, body = http("POST", "/requests", {"project_id": PID, "approver_id": APPROVER_ID,
                                          "amount_requested": "99999999.00", "purpose": "too much"}, REQ)
check(f"a request above the available budget is refused ({status})", status == 422)
r = ok(*http("POST", "/requests", {"project_id": PID, "approver_id": APPROVER_ID, "amount_requested": "500.00",
                                   "purpose": "Stale screen demo"}, REQ), expected=201)
stale = r["version"]
act(APPR, f"/requests/{r['id']}/reject", {"reason": "Duplicate"}, r["id"])
status, _ = http("POST", f"/requests/{r['id']}/approve", {"version": stale}, APPR)
check(f"an action from an out-of-date screen is refused ({status})", status == 409)
status, body = http("POST", f"/requests/{r['id']}/withdraw", {"version": detail(r["id"], REQ)["version"]}, REQ)
check(f"the requestor can withdraw the rejected request ({status})", status == 200)
status, _ = http("POST", f"/requests/{RID}/documents", token=REQ, multipart=("late.pdf", PDF))
check(f"receipts can't be changed after liquidation ({status})", status == 409)

# ============================================================================= 5. summary
stage("5. Where to look in the web app")
hist = ok(*http("GET", f"/requests/{RID}/history", token=REQ))
print(f"  {REF} history ({len(hist)} entries): " + " → ".join(h["action"] for h in hist))
ledger = ok(*http("GET", f"/projects/{PID}/ledger?size=6", token=FIN))["items"]
print(f"  {PROJECT['code']} latest ledger rows: " + ", ".join(f"{e['entry_type']} {rupees(abs(float(e['amount'])))}" for e in ledger))
print(f"\n  Open {UI}/requests/{RID}  (sign in as req.a / approver.a / finance@demo.local, password Demo@1234)")
print(f"       {UI}/requests/{RID2}")
print(f"       {UI}/projects/{PID}  →  Ledger tab")

passed = sum(checks)
print(f"\nWALKTHROUGH: {passed}/{len(checks)} checks passed")
sys.exit(0 if passed == len(checks) else 1)
