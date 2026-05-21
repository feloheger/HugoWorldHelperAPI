from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, field_validator
from datetime import datetime, timezone
from decimal import Decimal
import os
import asyncio
 
app = FastAPI()
 
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "geheim")
 
payments: dict[str, list[dict]] = {}
 
# Fix #1: Lock pro Sender gegen Race Conditions
_locks: dict[str, asyncio.Lock] = {}
 
def get_lock(sender: str) -> asyncio.Lock:
    key = sender.lower()
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]
 
 
# Fix #7: Secret als Header-Dependency statt im Body
def verify_secret(x_secret: str = Header(..., alias="X-Secret")):
    if x_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Falsches Secret")
 
 
class PaymentIn(BaseModel):
    sender: str
    receiver: str
    amount: Decimal  # Fix #6: Decimal statt float für Geldbeträge
    raw: str = ""
 
    # Fix #4: Negative und Null-Beträge ablehnen
    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("amount muss positiv sein")
        return v
 
 
class QueryIn(BaseModel):
    sender: str
 
 
class ClaimIn(BaseModel):
    sender: str
    min_amount: Decimal
 
    @field_validator("min_amount")
    @classmethod
    def min_amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("min_amount muss positiv sein")
        return v
 
 
def log(tag: str, msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [{tag}] {msg}", flush=True)
 
 
@app.get("/")
@app.head("/")  # Fix: HEAD-Requests für Render Health Check
def root():
    log("INFO", "GET / aufgerufen")
    return {"status": "ok"}
 
 
@app.post("/payment")
async def receive_payment(
    p: PaymentIn,
    _: None = Depends(verify_secret),  # Fix #7: Secret im Header
):
    log("PAYMENT", f"Eingehend → sender='{p.sender}' receiver='{p.receiver}' amount={p.amount} raw='{p.raw}'")
 
    key = p.sender.lower()
 
    # Fix #2: Server-seitiger Timestamp — nicht vom Client übernehmen
    server_ts = datetime.now(timezone.utc).timestamp()
 
    if key not in payments:
        payments[key] = []
 
    payments[key].append({
        "amount":   p.amount,
        "receiver": p.receiver.lower(),  # Fix #5: Normalisierung konsistent
        "ts":       server_ts,
        "used":     False,
        "raw":      p.raw,
    })
 
    total_stored = sum(x["amount"] for x in payments[key] if not x["used"])
    log("PAYMENT", f"✅ Gespeichert. Gesamt unverbraucht für '{p.sender}': ${total_stored}")
    return {"ok": True, "sender": p.sender, "amount": str(p.amount)}
 
 
@app.post("/query")
async def query_payments(
    q: QueryIn,
    _: None = Depends(verify_secret),  # Fix #7: Secret im Header
):
    log("QUERY", f"Anfrage für sender='{q.sender}'")
 
    key = q.sender.lower()
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
    alle = payments.get(key, [])
    gefiltert = [p for p in alle if p["ts"] >= cutoff and not p["used"]]
    total = sum(p["amount"] for p in gefiltert)
 
    log("QUERY", f"'{q.sender}' → {len(gefiltert)} Zahlungen gefunden → Summe: ${total}")
    return {"sender": q.sender, "total": str(total)}
 
 
@app.post("/claim")
async def claim_payment(
    c: ClaimIn,
    _: None = Depends(verify_secret),  # Fix #7: Secret im Header
):
    log("CLAIM", f"Claim für sender='{c.sender}' min_amount={c.min_amount}")
 
    key = c.sender.lower()
    lock = get_lock(key)
 
    # Fix #1: Lock verhindert Race Condition bei gleichzeitigen Requests
    async with lock:
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        available = [p for p in payments.get(key, []) if p["ts"] >= cutoff and not p["used"]]
        total = sum(p["amount"] for p in available)
 
        log("CLAIM", f"Verfügbar für '{c.sender}': ${total} (benötigt: ${c.min_amount})")
 
        if total < c.min_amount:
            log("CLAIM", f"❌ Nicht genug — abgelehnt")
            raise HTTPException(
                status_code=400,
                detail=f"Nur {total} verfügbar, {c.min_amount} benötigt"
            )
 
        # Fix #3: Nur so viele Zahlungen markieren wie nötig,
        # letzte Zahlung ggf. nur teilweise verbrauchen
        consumed = Decimal("0")
        for p in available:
            if consumed >= c.min_amount:
                break
            noch_benoetigt = c.min_amount - consumed
            if p["amount"] <= noch_benoetigt:
                # Gesamte Zahlung verbrauchen
                p["used"] = True
                consumed += p["amount"]
            else:
                # Zahlung aufteilen: nur den benötigten Rest abziehen
                p["amount"] -= noch_benoetigt
                consumed += noch_benoetigt
 
        remaining = total - consumed
        log("CLAIM", f"✅ {consumed}$ als verbraucht markiert. Verbleibend: ${remaining}")
        return {"ok": True, "consumed": str(consumed), "remaining": str(remaining)}
