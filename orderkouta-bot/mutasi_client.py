#!/usr/bin/env python3
# mutasi_client.py
import httpx
from typing import Dict, Any, List

async def fetch_mutasi(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = cfg["mutasi"]["url"]
    payload = {
        "auth_username": cfg["mutasi"]["auth_username"],
        "auth_token": cfg["mutasi"]["auth_token"],
    }
    timeout = cfg["mutasi"].get("timeout_sec", 15)
    async with httpx.AsyncClient(timeout=timeout, verify=cfg["mutasi"].get("verify_ssl", True)) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        txs = data.get("data") or data.get("mutasi") or data.get("transactions") or []
        norm = []
        for t in txs:
            amt = t.get("amount") or t.get("nominal") or t.get("credit") or t.get("debit")
            note = (t.get("note") or t.get("description") or t.get("remark") or "").strip()
            ref  = t.get("ref") or t.get("trx_id") or t.get("id")
            ts   = t.get("time") or t.get("timestamp") or t.get("date")
            # normalize amount to int rupiah
            val = None
            if amt is not None:
                s = str(amt).replace(".", "").replace(",", ".")
                try:
                    val = int(float(s))
                except Exception:
                    val = None
            norm.append({
                "amount": val,
                "note": note,
                "ref": str(ref) if ref is not None else None,
                "time": str(ts) if ts is not None else None,
                "raw": t,
            })
        return norm
