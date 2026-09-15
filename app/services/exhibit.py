from __future__ import annotations

from html import escape


def snapshot_svg(snapshot: dict) -> str:
    hops = snapshot["hops"]
    terminal = snapshot["terminal"]
    mode = str(snapshot.get("engine", {}).get("mode", "fixture")).upper()
    freshness = snapshot.get("data_freshness", {})
    as_of = freshness.get("chain_data_as_of_ms")
    provenance_line = f"{mode} MODE · chain data as of UTC ms {as_of}" if as_of else f"{mode} MODE"
    if not hops:
        kind = escape(str(terminal.get("kind", "trace_closed")).replace("_", " "))
        note = escape(str(terminal.get("note", "Trace closed without custody evidence.")))
        return "".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="280" viewBox="0 0 720 280">',
                '<rect width="100%" height="100%" fill="#eef1f6"/>',
                '<style>text{font-family:Arial,sans-serif}.mono{font-family:monospace}.node{fill:#fff;stroke:#ecd9a8;stroke-width:2}.caption{fill:#5c6880;font-size:13px}</style>',
                '<rect class="node" x="60" y="72" width="600" height="128" rx="10"/>',
                '<circle cx="88" cy="102" r="7" fill="#c98a12"/>',
                f'<text class="mono caption" x="60" y="38">{escape(provenance_line)}</text>',
                f'<text x="106" y="108" font-size="16" font-weight="700" fill="#111a2b">{kind}</text>',
                f'<text class="caption" x="88" y="148">{note}</text>',
                '<text class="mono caption" x="88" y="174">No custody finding was created from this snapshot.</text>',
                "</svg>",
            ]
        )
    has_custody_endpoint = bool(
        terminal.get("kind") in {"vasp_deposit", "verified_custody"}
        and terminal.get("hot_wallet")
    )
    width = 250 * len(hops) + (260 if has_custody_endpoint else 80)
    height = 360
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#eef1f6"/>',
        '<style>text{font-family:Arial,sans-serif}.mono{font-family:monospace}.node{fill:#fff;stroke:#d9dee5;stroke-width:2}.edge{stroke:#17427f;stroke-width:2.4}.attr{stroke:#16a34a;stroke-width:2;stroke-dasharray:7 5}.high{stroke:#16a34a}.medium{stroke:#c98a12}.low{stroke:#5c6880}.addr{fill:#17427f}.vasp{fill:#c98a12}.verified{fill:#16a34a}.caption{fill:#5c6880;font-size:11px}</style>',
        f'<text class="mono caption" x="34" y="38">{escape(provenance_line)}</text>',
    ]
    for index, hop in enumerate(hops):
        x = 34 + index * 250
        y = 105
        if index:
            prev = 34 + (index - 1) * 250
            parts.append(f'<path class="edge" d="M{prev+182} {y+48} L{x} {y+48}"/>')
            txid = escape(hop["txids"][0][:10] + "...")
            parts.append(f'<text class="mono caption" x="{prev+188}" y="{y+35}">{txid}</text>')
            parts.append(f'<text class="caption" x="{prev+188}" y="{y+75}">{hop["share_bp"]/100:.2f}% · {hop["value_base"]/1_000_000:,.2f} USDT</text>')
        band = hop.get("band", "low")
        kind = "verified" if hop["address"] == terminal.get("deposit_address") else "addr"
        parts.append(f'<rect class="node {band}" x="{x}" y="{y}" width="182" height="104" rx="10"/>')
        parts.append(f'<circle class="{kind}" cx="{x+18}" cy="{y+22}" r="6"/>')
        parts.append(f'<text x="{x+32}" y="{y+27}" font-size="13" font-weight="700" fill="#111a2b">H{hop["hop"]} {escape(hop["class"].replace("_", " "))}</text>')
        address = hop["address"]
        short = address[:8] + "..." + address[-6:]
        parts.append(f'<text class="mono" x="{x+14}" y="{y+58}" font-size="12" fill="#16233d">{escape(short)}</text>')
        parts.append(f'<text x="{x+14}" y="{y+84}" font-size="12" fill="#5c6880">{hop["value_base"]/1_000_000:,.2f} USDT · {hop["share_bp"]/100:.2f}%</text>')
    y = 105
    if has_custody_endpoint:
        last_x = 34 + (len(hops) - 1) * 250
        hot_x = last_x + 260
        parts.append(f'<path class="attr" d="M{last_x+182} {y+48} L{hot_x} {y+48}"/>')
        parts.append(f'<rect class="node high" x="{hot_x}" y="{y}" width="190" height="104" rx="8"/>')
        parts.append(f'<circle class="verified" cx="{hot_x+18}" cy="{y+22}" r="6"/>')
        parts.append(f'<text x="{hot_x+32}" y="{y+27}" font-size="13" font-weight="700" fill="#111a2b">VASP hot wallet</text>')
        short_hot = terminal.get("hot_wallet", "")[:8] + "..." + terminal.get("hot_wallet", "")[-6:]
        parts.append(f'<text class="mono" x="{hot_x+14}" y="{y+58}" font-size="12" fill="#16233d">{escape(short_hot)}</text>')
        parts.append(f'<text x="{hot_x+14}" y="{y+84}" font-size="12" fill="#5c6880">Coinsphere Global · registry</text>')
    for parked_index, branch in enumerate(snapshot.get("parked", [])):
        parent_x = 34 + branch["from_hop"] * 250
        py = 252 + parked_index * 44
        parts.append(f'<path d="M{parent_x+91} {y+104} L{parent_x+91} {py}" stroke="#c98a12" stroke-width="1.6" stroke-dasharray="4 4"/>')
        parts.append(f'<rect x="{parent_x+8}" y="{py}" width="168" height="30" rx="8" fill="#fdf6e8" stroke="#ecd9a8"/>')
        parts.append(f'<text x="{parent_x+18}" y="{py+20}" font-size="11" fill="#9a6b06">Parked {escape(branch["branch_id"])} · {branch["value_base"]/1_000_000:,.2f} USDT</text>')
    parts.append("</svg>")
    return "".join(parts)
