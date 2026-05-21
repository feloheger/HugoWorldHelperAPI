from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import os

app = FastAPI()

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "geheim")

payments: dict[str, list[dict]] = {}


class PaymentIn(BaseModel):
    sender: str
    receiver: str
    amount: float
    timestamp: int
    secret: str
    raw: str = ""

class QueryIn(BaseModel):
    sender: str
    secret: str

class ClaimIn(BaseModel):
    sender: str
    min_amount: float
    secret: str


def log(tag: str, msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [{tag}] {msg}", flush=True)


@app.get("/")
def root():
    log("INFO", "GET / aufgerufen")
    return {"status": "ok"}


@app.post("/payment")
def receive_payment(p: PaymentIn):
    log("PAYMENT", f"Eingehend → sender='{p.sender}' receiver='{p.receiver}' amount={p.amount} raw='{p.raw}'")

    if p.secret != WEBHOOK_SECRET:
        log("AUTH", f"Falsches Secret von sender='{p.sender}' — abgelehnt")
        raise HTTPException(status_code=403, detail="Falsches Secret")

    key = p.sender.lower()
    if key not in payments:
        payments[key] = []

    payments[key].append({
        "amount":   p.amount,
        "receiver": p.receiver,
        "ts":       p.timestamp,
        "used":     False,
        "raw":      p.raw,
    })

    total_stored = sum(x["amount"] for x in payments[key] if not x["used"])
    log("PAYMENT", f"✅ Gespeichert. Gesamt unverbraucht für '{p.sender}': ${total_stored}")
    return {"ok": True, "sender": p.sender, "amount": p.amount}


@app.post("/query")
def query_payments(q: QueryIn):
    log("QUERY", f"Anfrage für sender='{q.sender}'")

    if q.secret != WEBHOOK_SECRET:
        log("AUTH", f"Falsches Secret bei /query für '{q.sender}' — abgelehnt")
        raise HTTPException(status_code=403, detail="Falsches Secret")

    key = q.sender.lower()
    cutoff = datetime.now(timezone.utc).timestamp() - 86400

    alle = payments.get(key, [])
    gefiltert = [p for p in alle if p["ts"] >= cutoff and not p["used"]]
    total = sum(p["amount"] for p in gefiltert)

    log("QUERY", f"'{q.sender}' → {len(gefiltert)} Zahlungen gefunden → Summe: ${total}")
    return {"sender": q.sender, "total": total}


@app.post("/claim")
def claim_payment(c: ClaimIn):
    log("CLAIM", f"Claim für sender='{c.sender}' min_amount={c.min_amount}")

    if c.secret != WEBHOOK_SECRET:
        log("AUTH", f"Falsches Secret bei /claim für '{c.sender}' — abgelehnt")
        raise HTTPException(status_code=403, detail="Falsches Secret")

    key = c.sender.lower()
    cutoff = datetime.now(timezone.utc).timestamp() - 86400

    available = [p for p in payments.get(key, []) if p["ts"] >= cutoff and not p["used"]]
    total = sum(p["amount"] for p in available)

    log("CLAIM", f"Verfügbar für '{c.sender}': ${total} (benötigt: ${c.min_amount})")

    if total < c.min_amount:
        log("CLAIM", f"❌ Nicht genug — abgelehnt")
        raise HTTPException(status_code=400, detail=f"Nur {total} verfügbar, {c.min_amount} benötigt")

    consumed = 0.0
    for p in available:
        if consumed >= c.min_amount:
            break
        p["used"] = True
        consumed += p["amount"]

    log("CLAIM", f"✅ {consumed}$ als verbraucht markiert. Verbleibend: ${total - consumed}")
    return {"ok": True, "consumed": consumed, "remaining": total - consumed}
