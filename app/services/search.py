from __future__ import annotations

from difflib import SequenceMatcher

from app.services.demo import demo_case


def search_records(query: str, limit: int = 10) -> list[dict]:
    q = query.strip().lower()
    if not q:
        return []
    data = demo_case()
    candidates = []
    case = data["case"]
    candidates.append(("case", case["ack_no"], case["ack_no"], "/docket"))
    candidates.append(("address", case["reported_address"], case["reported_address"], "/cases/new"))
    candidates.append(("tx", case["payment_txid"], case["payment_txid"], "/docket"))
    candidates.append(("notice", data["notice"]["notice_no"], data["notice"]["notice_no"], "/notices"))
    candidates.append(("entity", data["entity"]["name"], data["entity"]["name"], "/docket"))
    for hop in data["dominant_path"]:
        candidates.append(("address", hop["address"], hop["address"], "/docket"))

    ranked = []
    for kind, label, value, href in candidates:
        lowered = value.lower()
        if lowered == q:
            score = 100
        elif lowered.startswith(q):
            score = 92
        elif q in lowered:
            score = 80
        else:
            score = round(SequenceMatcher(None, q, lowered).ratio() * 60)
        if score >= 35:
            ranked.append({"kind": kind, "label": label, "value": value, "href": href, "score": score})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
