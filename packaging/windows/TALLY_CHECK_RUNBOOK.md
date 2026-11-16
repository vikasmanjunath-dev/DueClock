# Real TallyPrime check: runbook (about 20 minutes on a PC that has TallyPrime)

Purpose: find out which of our read-only Tally requests the customer's TallyPrime accepts, and whether our parser reads the reply. Until this is done once on a real Tally, the request and reply shapes are unverified assumptions.

## Rehearse first on any computer (no Tally needed)
```
python3 -m dueclock.connector.demo_tally --port 9000        # window 1: a pretend Tally with fictional bills
python3 -m dueclock.connector tally-check                    # window 2: expect PASS
```
Variants to rehearse failures: `--utf16`, `--amount-style dr`, `--amount-style negative`, `--plain-only` (first request refused, fallback used). This only proves our own code; it says nothing about the real product.

## On the real PC
1. **Prepare Tally.** Open TallyPrime with the customer's company loaded, one that has outstanding customer bills. Enable its HTTP/XML server on port 9000 (TallyPrime: Help > Settings > Connectivity; [Tally's own guide](https://help.tallysolutions.com/xml-integration/)). Menu names vary by release: follow Tally's guide if they differ.
2. **Install the connector** (installer, or the exe from `dist\dueclock-connector`). Setup runs `tally-check` once; you can run it again any time.
3. **Run the check** (read-only; it never writes to Tally):
   ```
   dueclock-connector tally-check --save-profile
   ```
   Add `--company "Exact Company Name"` if several companies are loaded.
4. **Read the verdict.**
   - `PASS`: the variant that worked is saved in the config; nothing more to do for the request. Go to step 6.
   - `PARTIAL`: warnings only (for example several companies loaded, duplicate bills, company list not available). Read each line; none blocks syncing.
   - `FAIL`: see the table below.
5. **Send back** the folder named in the last line (`...\DueClock\tally-check\<time>\`). It holds `tally_check_report.json` and REDACTED replies (names, invoice numbers and free text replaced by placeholders; dates, amounts and structure kept). Check one file in a text editor before sending if you like.
6. **Compare with Tally by eye** (the check cannot do this): pick 3 bills in Tally's Bills Receivable screen; their date, reference, party, pending amount and due date must equal the DueClock invoice list after `dueclock-connector run-once`.
7. **Keep the capture as a permanent test** (on the development machine, in the repository folder):
   ```
   python -m dueclock.connector tally-fixture <path to bills_*.xml> --name prime-<version>
   ```
   It re-redacts, refuses captures the parser cannot fully read, and saves `tests/fixtures/tally/real_prime-<version>.xml`; `pytest tests/connector/test_tally_check.py` then replays it forever.

## FAIL and what to do
| Finding | Meaning | Action |
|---|---|---|
| `TALLY_UNREACHABLE` | nothing answered on the port | Tally not running, server not enabled, or another port; fix and rerun |
| `BILLS_REQUEST_FAILED` | no request variant returned readable XML | send the folder; a new variant or a hand-written request file (`tally_bills_template_path`, `{company}` placeholder, read-only exports only) is needed |
| `PARSER_SHAPE_UNKNOWN` | Tally answered; our parser does not know the shape (tags are listed) | send the folder; the parser is extended from the capture |
| `ZERO_BILLS` | valid reply, no bills | company has no open receivables, or the report needs different settings |
| `PARSE_ERRORS` | some rows unreadable | send the folder |

## What a pass still does NOT prove
- Other Tally versions or configurations (repeat on each new customer's first install; the check takes one minute).
- That credit days are agreements: DueClock infers them from Tally's due date. The owner confirms written agreements in the app.
- Multi-company behaviour beyond the company you named.
