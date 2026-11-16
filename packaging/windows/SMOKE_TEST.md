# Windows install smoke test (first 5 pilot installs)

Run on a real Windows 10/11 PC with TallyPrime open (HTTP port enabled). Record each result; the .NET switch (D-03) is decided by items 2 and 3.

1. **Build**: `packaging\windows\build.ps1` finishes; the setup exe and its `.sha256` exist in `dist\`.
2. **SmartScreen**: double-click the setup. Expect "Windows protected your PC" (unsigned). Click *More info → Run anyway*. Record: blocked outright? (counts as a block)
3. **Antivirus**: note any product that quarantines or flags the setup or `dueclock-connector.exe`. (Counts as a block.)
4. **Install**: enter cloud address, access key, port. Setup completes with no error dialog and no administrator prompt.
5. **Files**: `%LOCALAPPDATA%\DueClock\connector.json` exists and contains `api_key_protected` (starts with `dpapi:`), and does NOT contain the key.
6. **Task**: `schtasks /Query /TN "DueClock Connector"` shows it; it is hidden, runs at sign-in and hourly.
7. **Tally**: `dueclock-connector doctor` shows tally running and cloud ok.
8. **First sync**: `dueclock-connector run-once`, then check the invoices appear in the DueClock app. Confirm Tally shows no change (read-only).
9. **Copy test**: copy `connector.json` to another PC/user: `doctor` must fail with "stored access key cannot be read" (DPAPI binding).
10. **Uninstall**: Apps list → uninstall removes the task (`schtasks /Query` says not found); answering Yes deletes `%LOCALAPPDATA%\DueClock`.
11. **Tally request (the real check)**: with Tally open on a company that has outstanding bills, run `dueclock-connector tally-check --save-profile`. Expect `PASS` (or `PARTIAL` with explained warnings). The report and REDACTED replies are in `%LOCALAPPDATA%\DueClock\tally-check\<time>\`. Copy the `bills_*.xml` file that worked to `tests\fixtures\tally\real_<tally-version>.xml` in the repo: `pytest tests/connector/test_tally_check.py` then replays it through the parser. On `FAIL`, send the whole folder (it contains no names or invoice numbers); `PARSER_SHAPE_UNKNOWN` lists the tags Tally returned. If no built-in request works, put a working request in a file with a `{company}` placeholder and set `tally_bills_template_path` in the config (read-only exports only).
12. **Compare with Tally**: pick 3 bills in Tally's Bills Receivable screen; their date, reference, party, pending amount and due date must match the DueClock invoice list exactly.
