from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import os
 
app = FastAPI()
 
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "geheim")
 
# In-Memory: { "spielername_lower": [{"amount": 400.0, "receiver": "Hugo", "ts": 123, "used": False}] }
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
 
 
@app.get("/")
def root():
    return {"status": "ok"}
 
 
@app.post("/payment")
def receive_payment(p: PaymentIn):
    """Fabric-Mod sendet hier jede erkannte Zahlung."""
    if p.secret != WEBHOOK_SECRET:
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
    return {"ok": True, "sender": p.sender, "amount": p.amount}
 
 
@app.post("/query")
def query_payments(q: QueryIn):
    """
    Bot fragt: Wie viel hat 'sender' in den letzten 24h gezahlt (unverbraucht)?
    Gibt die Summe aller unbenutzten Zahlungen der letzten 24h zurück.
    """
    if q.secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Falsches Secret")
 
    key = q.sender.lower()
    cutoff = datetime.now(timezone.utc).timestamp() - 86400  # 24h
 
    total = sum(
        p["amount"]
        for p in payments.get(key, [])
        if p["ts"] >= cutoff and not p["used"]
    )
    return {"sender": q.sender, "total": total}
 
 
@app.post("/claim")
def claim_payment(c: ClaimIn):
    """
    Bot ruft dies auf wenn Verifikation erfolgreich war.
    Markiert genug Zahlungen als 'used' damit sie nicht nochmal genutzt werden.
    """
    if c.secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Falsches Secret")
 
    key = c.sender.lower()
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
 
    available = [
        p for p in payments.get(key, [])
        if p["ts"] >= cutoff and not p["used"]
    ]
 
    total = sum(p["amount"] for p in available)
    if total < c.min_amount:
        raise HTTPException(status_code=400, detail=f"Nur {total} verfügbar, {c.min_amount} benötigt")
 
    # Zahlungen als used markieren bis min_amount erreicht
    consumed = 0.0
    for p in available:
        if consumed >= c.min_amount:
            break
        p["used"] = True
        consumed += p["amount"]
 
    return {"ok": True, "consumed": consumed, "remaining": total - consumed}
