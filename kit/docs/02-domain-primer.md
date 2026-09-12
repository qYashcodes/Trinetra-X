# 02 · Domain primer (for engineers)

Compressed from the team's domain brief. Read once; it prevents the expensive mistakes.

## 1. The ledger

- A blockchain is a **public, append-only ledger**. Anyone can read it; no warrant needed. This is why the problem is solvable.
- **Address** = a public identifier (TRON: starts `T`, 34 chars, base58check, version byte `0x41`). Free, unlimited, no identity attached.
- **Wallet** = software holding keys; one wallet controls many addresses. Be precise; don't use the words interchangeably.
- A **transaction** records only: hash (64 hex), from, to, amount, token, timestamp/block. No memo, no identity, no jurisdiction.
- **Pseudonymous, not anonymous.** Tracing where money went is easy. Knowing who owns an address is the actual problem.
- **Irreversible.** No chargeback. The only remedy is legal and off-chain, against a company.

## 2. Coins, tokens, USDT on TRON

- Native coin = the courier's postage (TRX on TRON), paid as **gas**. A **token** (USDT) is a balance kept by a smart contract, the parcel being carried.
- A wallet with USDT and zero TRX cannot move funds. Someone had to send the burner its TRX. **Gas funding is a strong clustering signal** (v2 feature).
- USDT is a stablecoin (~1 USD). Criminals use it for **price stability** (they run payroll, not a portfolio), secondarily liquidity.
- USDT has an **issuer (Tether) that can freeze any address**. Second choke point when funds sit still with no exchange involved. Verify Tether's current LE process before putting it on a slide.
- Same brand, separate ledgers: TRC-20 USDT (TRON) ≠ ERC-20 USDT (Ethereum).

| Network | Address format |
|---|---|
| TRON | `T…`, 34 chars, base58check |
| EVM family (Ethereum, BSC, Polygon) | `0x…`, 42 chars. **Same string exists on all EVM chains with different balances.** `0x` ≠ Ethereum |
| Bitcoin | `1…`, `3…`, `bc1…` |

**Why TRON first:** UNODC reporting names USDT on TRON as the preferred instrument for cyber-fraud and laundering networks in East and Southeast Asia; Chainalysis identified over $12B in scam-linked USDT on TRON in 2025. Indian victims of Myanmar/Cambodia compound scams (investment and task fraud, the first typologies in the PS) sit squarely on this rail. *Re-verify figures before slides.*

## 3. Reading the chain

TronGrid: `GET https://api.trongrid.io/v1/accounts/{address}/transactions/trc20` returns every
TRC-20 transfer the address is party to (dual-indexed by sender and receiver).

One item, stripped: `transaction_id`, `block_timestamp`, `from`, `to`, `value` (e.g. `"48500000000"`), `token_info {symbol, decimals, address}`.

**Two silent traps** (never crash, just make every number wrong):
- `value` is base units. `decimals: 6` → 48,500,000,000 / 10⁶ = **48,500 USDT**. Read decimals from the response.
- `block_timestamp` is **milliseconds**. Without `/1000`, dates land in year ~57,000.

**Direction.** Relative to the investigated address, a transfer is incoming or outgoing. Tracing forward follows outgoing only. Each jump = one **hop**.

Incoming transfers still matter: other senders to the same burner are probable **unreported victims**; many small deposits from unrelated addresses over days is consistent with mass collection; three complaints feeding one next hop = **one syndicate**, not three cases.

## 4. The laundering pipeline

Placement (victim pays; done before we get the case) → **Layering** (break the link; our battleground) → Integration (clean rupees out). We race to reach the start of integration.

| Shape | What happens | How the engine treats it |
|---|---|---|
| Fan-out | 50k in → 10 × 5k to fresh addresses | Breadth cap 3 + value floor; attack on investigator attention costs us little |
| Peel chain | 50k in → 3k peeled off, 47k forwarded, repeat | Follow the bulk; peel recorded as a parked branch |
| Consolidation | Many → one → forwards nearly everything | Same shape as a deposit address; separated by the classifier |

**The explosion problem.** Follow every edge with 5 outputs per address: 6 hops = 15,625 addresses, ~52 min, a rate-limit ban, an unreadable graph. Fix: follow the money, not the edges — value floor, time window, depth cap, breadth cap, stop conditions. A trace becomes 15–40 API calls.

**Worked example (the two filters do different jobs).** Victim paid 50,000 at 22:14 on 14 Aug. The reported address shows 49,200 out at 22:31 (98%, follow), 12 out at 22:16 (dust, log only), and four transfers from March last year (rejected by the **time window** before value is even considered).

**Bridges.** Only amount and time survive a chain crossing. Match similar value within minutes on the destination chain; confidence scales with how unusual the amount is (49,183.47 is near-unique; 50,000.00 isn't). Always "candidate", never "the funds went here". Not built in v1.

## 5. The vault and box 4471

A bank branch has 50,000 numbered drop-boxes. Box 4471 is yours. Within minutes a clerk empties it into one famous vault. The vault is advertised; the boxes are unmarked, but the bank's internal list says box 4471 = this PAN, Aadhaar, bank account.

Police following the money reach the vault, and it's useless: everyone's cash is mixed there. They needed the **box number**.

How do you know 4471 is a bank drop-box? Watch it: strangers keep putting money in, it's emptied within minutes, always into the same vault, never holds anything overnight. Then count the boxes feeding that vault: if 40,000 boxes empty into it, it's a bank. Therefore each feeder is a customer box. **You identify the box by finding what it feeds.**

| Story | Real term |
|---|---|
| Numbered drop-box | Deposit address |
| Giant vault | Exchange hot wallet |
| The bank | Exchange / VASP |
| Clerk emptying the box | Sweep |
| Bank's internal customer list | KYC records |
| Watching the box's habits | Behavioural heuristic (classifier) |
| 40,000 boxes, one vault | Hub feed count / consolidation |

### The fingerprint

| Behaviour | Deposit address | Merchant | Collection wallet | Personal |
|---|---|---|---|---|
| Many unrelated senders | Yes | Yes | Yes | No |
| Forwards ~100% | Yes | No | Usually | No |
| Always same destination | Yes | Sometimes | Sometimes | No |
| Near-zero balance | Yes | No | Often | No |
| Swept within minutes | Yes | No | Variable | No |
| Destination fed by thousands of identical addresses | **Yes** | No | No | No |

The last row is the core innovation. Lookalikes: payment gateways (smaller scale), mixer payout addresses. Volume, hub size, sweep regularity separate them, which is why we emit a **score with reasons, never a label**.

## 6. The legal endgame

- **VASP** = Virtual Asset Service Provider. Brought under **PMLA in March 2023** as reporting entities (KYC + duty to respond). Obligation is activity-based, so offshore VASPs serving Indians are covered. Registration is with **FIU-IND** (team brief cites 49 registered in FY 2024-25: 45 domestic, 4 offshore; re-verify).
- **NCRP / CFCFRMS** = where complaints come from (our input). **SAHYOG** (MHA portal run by I4C, launched Oct 2024) = channel for notices to intermediaries under **IT Act s.79(3)(b)** (our output channel, future).
- Notice legal basis in the demo: Section 106 BNSS (seizure) read with PMLA reporting-entity obligations. **Final wording is owned by the team's legal/investigator member.**
- Maintain a lookup of registered domestic VASPs, registered offshore VASPs, and known non-compliant platforms. An unregistered endpoint must produce "recovery probability low, no Indian legal hook", not a fake freeze promise.

| Stage | Typical elapsed time |
|---|---|
| Victim realises fraud | hours–days |
| Complaint filed | + hours |
| Funds at deposit address | minutes–hours |
| Exchange sweeps to hot wallet | minutes |
| Withdrawal to bank / P2P | hours |

**Report must contain:** complaint reference and reported address; full hop path with amounts, timestamps, tx hashes; terminal point; confidence and reasoning; recommended action with draft notice. **Hashes turn intelligence into evidence**: anyone can re-verify against the public chain.

## 7. Misconceptions that cost marks

- "Burner = unsolvable." Burner means no name, not invisible. It's our input.
- "Ask the blockchain to reverse it." No operator; irreversible by design.
- "Mixers make it hopeless." Over half of illicit funds go straight to centralised exchanges; ~a tenth to mixers, dominated by ransomware/darknet, not our typologies.
- "400 incoming transfers = scammer." Also describes exchanges, merchants, gateways, donation addresses. Pattern ≠ proof.
- "Reached the hot wallet = found the fraudster." You're outside the vault. The account is one hop earlier.
- "The deposit address is the scammer's wallet." It's an exchange customer's drop-box, possibly a mule or an innocent P2P seller. Only legal process reveals whose.
- "Keeps some, forwards the rest = peel chain." Opposite. A peel chain forwards the bulk and keeps little.
- "Let's add Ethereum, it's a bit more work." It doubles surface area where this fraud doesn't live. Spend the time on the classifier.

## 8. What nobody can do (a slide, not a secret)

Mixers, privacy coins, non-custodial DeFi, and unregistered offshore platforms end the legal trail for everyone, including Chainalysis and the FBI short of multi-year operations. A limit shared by the whole industry is not a flaw in this project. Reporting a dead end with timestamped evidence saves officer-days.

## 9. Competitive position

Chainalysis's advantage is its labelled dataset, not a secret algorithm. Account-model chains (TRON) are under-covered by the academic clustering literature (UTXO heuristics like common-spend and change-address don't transfer). Our differentiator is **behavioural deposit-address attribution on an account-model chain, with case memory**, at ₹0 infrastructure cost, deployable to a district cell without procurement.
