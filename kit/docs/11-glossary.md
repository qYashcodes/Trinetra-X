# 11 · Glossary

## Domain

| Term | Meaning |
|---|---|
| Address | Public identifier holding funds. Free, unlimited, no identity attached |
| Wallet | Software holding keys; controls many addresses |
| Tx hash / txid | 64-hex unique ID of a transaction; lets anyone verify it independently |
| Coin / token | Native currency (TRX) vs a contract-tracked balance (USDT) |
| Gas | Fee paid in the native coin to move anything |
| TRC-20 / ERC-20 | Token standards on TRON / Ethereum; same USDT brand, separate ledgers |
| Stablecoin | Token pegged to a currency; USDT ≈ 1 USD |
| Hop | One jump along an outgoing transfer |
| Burner wallet | Fresh address used briefly and abandoned; our typical input |
| Placement / layering / integration | Laundering stages; we operate in layering |
| Peel chain | Small amounts peeled off repeatedly while the bulk moves on |
| Fan-out | One address splitting into many; attacks investigator attention |
| Consolidation | Many addresses feeding one |
| Deposit address | Per-customer exchange drop-box; unlabelled; maps to KYC. The legally significant object |
| Hot wallet | Exchange's pooled, publicly known wallet; a landmark, names the company not the customer |
| Sweep | Exchange moving a deposit into its hot wallet, usually within minutes |
| Mixer | Pools funds to destroy the in/out link; a hard stop |
| Bridge | Moves value between chains; links are inferred, never certain |
| Non-custodial / DeFi | No company holds keys or operates the contract; no one to serve a notice on |
| VASP | Virtual Asset Service Provider; a company handling crypto for customers |
| KYC | Know Your Customer identity record a VASP must hold |
| FIU-IND | Financial Intelligence Unit India; VASPs register here under PMLA |
| PMLA | Prevention of Money Laundering Act; VASPs are reporting entities since March 2023 |
| NCRP / CFCFRMS | National cybercrime reporting portal and its financial-fraud system; our input |
| SAHYOG | MHA/I4C portal for notices to intermediaries under IT Act s.79(3)(b); future output channel |
| I4C | Indian Cyber Crime Coordination Centre |
| CCTNS | Crime and Criminal Tracking Network & Systems (police records) |
| Parichay | NIC's single sign-on for government applications |

## Engine

| Term | Meaning |
|---|---|
| Taint | Portion of an address's balance attributed to the victim's funds |
| Haircut taint | Each outflow carries taint proportional to the tainted share of the balance at that moment |
| Best-first / value-priority | Always expand the address holding the most taint next |
| Dominant fund flow | Strategy: expand only the largest outflow per hop; others parked |
| Value-weighted | Strategy: expand every significant outflow |
| Value floor | 2% of the reported amount; below it an outflow is dust |
| Dust | Outflows below the floor: logged, not followed |
| Parked branch | Significant outflow off the dominant flow: recorded, classified one hop deep, not expanded |
| Time window | 8 h per hop after tainted funds arrive; older history never charged |
| Address budget | Max 60 addresses per trace |
| Seed matching | Accepting an on-chain transfer as the victim's payment only within 3% (min 1 USD) |
| Likelihood ratio (LR) | P(observation ∣ deposit address) / P(observation ∣ anything else) |
| Damping | Raising the LR product to 0.6 because signals are correlated |
| Posterior / band | Probability the address is a deposit address; high ≥ 0.85, medium 0.60–0.85, low < 0.60 |
| Custody point | Address with deposit posterior ≥ 0.60 or a labelled service |
| Hub-first inference | Classify the sweep destination first; the hub explains its feeders |
| Rung | Which of the 5 VASP-resolution evidence levels fired; sets the band |
| Terminal kind | How a trace ended (docs/06 §3) |
| Union-Find linkage | Grouping cases that share a deposit address or non-service intermediate |
| Fixture mode | Engine reads TronGrid-shaped JSON from `fixtures/` instead of the network |

## Application

| Term | Meaning |
|---|---|
| IO | Investigating Officer |
| Snapshot | One versioned, hashed run of a trace for a case; restart creates a new version and supersedes the old |
| Trace event | One persisted SSE event (`seq` ordered); the replay log |
| Finding / custody finding | The verdict derived from a snapshot's terminal; gated by four review checks |
| Countersignature | Supervising officer's sign-off, required at ≥ 10,000 USDT, never by the drafter |
| Dispatch | One channel's delivery of a notice (LE portal, compliance email, nodal copy) |
| Custodian response | The VASP's recorded reply: KYC disclosed, amount restrained; feeds rung 4 |
| Exhibit | Server-generated SVG of the trace graph for the report PDF |
| Verification appendix | Report section listing every txid, explorer URL, snapshot hash, engine version, and the command that reproduces the trace |
| Demo clock | `DEMO_CLOCK` env var that pins "now" so ages and deadlines render as designed |
