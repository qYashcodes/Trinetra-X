# 01 · Product brief

## The problem

Every day in India, victims of investment scams and "work from home" task frauds send money that
ends up as cryptocurrency. Their NCRP complaint contains one long string: a wallet address. In most
stations the trail goes cold there. Not because it is untraceable (the ledger is public) but because
tracing needs expertise and hours that a station with forty open cases doesn't have.

PS 26183 asks for real-time identification of fraud-linked exchanges from victim-reported wallet
addresses through automated blockchain analytics.

## What TRINETRA is

A desk tool for a district or state cyber cell. It turns **one reported address into one
investigative decision in under a minute, every time.**

> We don't "trace crypto". We compress the freeze window from weeks to minutes.

## Users

| User | What they do in TRINETRA |
|---|---|
| Investigating Officer (IO) — e.g. Insp. R. Kulkarni, Cyber Cell Pune City | Ingests the complaint, runs the trace, reviews the custody finding, drafts and dispatches the freeze notice, records the custodian's reply |
| Supervising Officer — e.g. ACP S. Deshmukh | Countersigns notices at or above 10,000 USDT from their own session |
| Exchange compliance desk (external) | Receives the notice with hashes; replies with KYC and restraint status (outside the system; reply is recorded by the IO) |
| State nodal officer (external, copy) | Receives a copy of dispatched notices |

## Where TRINETRA sits in the chain of custody

1. Victim calls 1930 / files on NCRP; complaint includes a wallet address.
2. IO picks it up. **Today, crypto cases stall here.**
3. **TRINETRA:** address in → trace → VASP named → confidence scored → notice drafted.
4. IO issues a preservation / information request to the VASP.
5. VASP responds with identity (PAN, Aadhaar, bank account, IP logs) and ideally a frozen balance.
6. Identity feeds the real investigation.

Our entire value is the time removed between steps 2 and 4.

## Two outputs, two expiry dates

- **Recovery** expires in hours: deposit → sweep → withdrawal to bank/P2P happens fast.
- **Identification** never expires: the KYC record behind a deposit address outlives the balance.
  A three-day-old complaint still yields the account holder's identity, the cash-out bank account
  (which Indian law freezes well), and deposit-address reuse that links complaints across states.

Pitch this explicitly. Teams that only pitch recovery get flattened by "what if the complaint is three days old?"

## Core insight (the thing nobody else will have)

The exchange **deposit address** is the only on-chain point that maps to a KYC'd identity, and it
appears in no public database. We find it **by behaviour**: many unrelated senders, ~100% forwarded,
near-zero resting balance, swept within minutes, always to the same destination, and that
destination is fed by hundreds of identical addresses. **We identify the hub, and the hub explains
every address feeding it.** (docs/02 § The vault and box 4471.)

## Scope

| In v1 | Out of v1 (stated openly) |
|---|---|
| TRON, USDT-TRC20 | EVM and BTC tracing (detected → "adapter in progress") |
| Reported address or payment tx hash as seed | Bridge continuation matching (v1 stops at `bridge` terminal) |
| Haircut-taint best-first traversal, 5 hops | Pre-transaction risk check against the live chain for unknown addresses beyond the scoring in Risk Check |
| 6-signal deposit-address classifier, 5-rung VASP ladder | Calibrated ML model (v2; v1 is an explainable Bayesian model) |
| Union-Find case linkage | Real CCTNS / Parichay SSO, real NCRP feed, real LE portal (need government onboarding) |
| 8-screen investigator application, freeze notice PDF, dispatch | Dark theme, Hindi, WebAuthn, fuzzy search, network/case canvas views |

## Every trail end is a useful output

| Trail ends at | Output | Useful because |
|---|---|---|
| Deposit address at a registered exchange | Named VASP, confidence, draft notice | The core win |
| Labelled hot wallet (no deposit address isolated) | Named VASP, notice to identify the crediting account | Freeze request |
| Funds unmoved at an address | "Funds live, escalate now" | Best case; issuer (Tether) freeze possible |
| Mixer | Trail terminates, evidence timestamped | Stops wasted officer-hours; pivot to mule bank accounts |
| Bridge | Exit chain, amount, time | A lead, not a dead end |
| Unregistered offshore VASP | Named entity, "recovery probability low, no Indian legal hook" | Sets expectations honestly |
| Unsupported chain | "Chain detected, adapter in progress" | Honest, not a crash |

Count the rows that say "system failed": zero. This table goes in the deck, unprompted.

## PS requirement → how we answer it

| PS asks for | Our answer | Honest status |
|---|---|---|
| Multiple blockchain ecosystems | Chain-adapter interface (`matches / fetch / normalise / explorer_url`); TRON implemented, EVM/BTC scaffolded | TRON only in v1 |
| Scalable indexing | TronGrid is already a dual-indexed node+indexer; we cache every response | Done |
| Real-time tracing | TRON blocks ~3 s; a trace is 15–40 API calls, seconds | Done in fixture; live multi-hop to verify |
| AI/ML-assisted risk detection | Bayesian likelihood-ratio classifier + case memory that improves with every VASP reply; calibration against self-labelled data planned (docs/10 D-09) | Reasoned, not yet calibrated. Judges will probe this. |
| Risk categorisation | Terminal kinds + confidence bands + FIU-IND registration status of the custodian | Done |
| Automated investigative recommendations | Terminal-specific next action + drafted notice | Done in design |
| Multiple tokens | USDT-TRC20 contract filter; token decimals read from response | USDT only |

## How to say it

- Never "we only did TRON". Say: *"We built a chain-agnostic adapter architecture and implemented
  TRON first, because UNODC and Chainalysis data show it is the dominant rail for exactly these
  fraud typologies. EVM is scaffolded."*
- *"We do value-weighted traversal, so laundering fan-out costs the attacker effort but costs us almost nothing."*
- *"A deposit address identifies an account, not a person. We ask the exchange to disclose; we never accuse."*
- Present limitations on a slide before anyone asks.

## Five sentences to remember

1. The chain tells us where the money went with certainty, and nothing about who owns it.
2. Nothing on-chain is reversible, so recovery is a legal action against a company.
3. The exchange deposit address is the only point where an address maps to a KYC'd identity, and it's in no database, so we find it by behaviour.
4. We identify the hub, and the hub explains every address feeding it.
5. Our product is not tracing; it is compressing the freeze window from weeks to minutes, with an honest answer every time.

## Demo success criteria

- Login → intake → live trace → canvas → verdict → notice dispatched, end-to-end, under 5 minutes of stage time; the trace itself under 60 s.
- Runs with Wi-Fi off (fixture mode).
- Any team member can explain any screen and the function behind it.
- One live-chain trace ready as a backup proof that it isn't only fixtures (docs/09 § Live verification).
