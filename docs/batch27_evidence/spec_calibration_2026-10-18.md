# Batch27 independent spec calibration — 2026-10-18

This records test-fixture corrections. It does not waive any product assertion.

## Preserved first specs and receipts

The three original source files were copied byte-for-byte into
\`docs/batch27_evidence/initial_specs/\` before either correction:

| File | Original SHA-256 |
|---|---|
| test_batch27_observation_audit.py | 754913f4edfdc3cb77b7e22d65ac64140a7754aba64ee3b0686bda35d5916ae1 |
| test_batch27_daily_report_volume.py | d063e2e5f289da20e61ea54d9cee8472caf9467789b8bce85b33501ca01d2c62 |
| test_batch27_logging_volume.py | 4010cb27dc176a4167c7de8201db37ed6ab2314e73e15db85e373c4f77026b6f |

Original receipts, including \`candidate_initial_69.json/.txt/.xml\` and
the B26/B27b initial red receipts, remain unchanged.

ROOT reported the first candidate result as 63 passed / 6 failed / 0 errors.
The four audit-pressure cases completed their full simulated duration before
failing the shutdown-time assertion. They are not discarded short replays.

## Corrections

1. **Final confirmation time versus shutdown completion time.** The four
   pressure cases compared a persisted broker confirmation with the clock
   *after* shutdown's final native queries. Those queries advance the offline
   clock by two seconds; they do not establish a newer confirmation.
   The corrected test first proves every row equals the actual final
   confirmation time immediately before shutdown, then requires every row to
   retain exactly that value after shutdown. It also records both times.
   No production timestamp refresh was added.

2. **SDK error callback signature.** The warning-preservation case reached its
   native error call after the INFO suppression assertion passed. Installed
   ib_insync requires the fourth \`advancedOrderRejectJson\` argument. The
   corrected call supplies the empty string. This corrects fixture TypeError,
   not broker error handling or warning semantics.

3. **Real product failure retained.** The ordinary-events test observed 201
   Events lines. Its upper bound remains 200. This is a product off-by-one
   finding, not a fixture correction.

All other assertions are unchanged. The calibrated specs must be rerun on both
isolated B26 and B27b baselines; the old receipts are not relabelled as receipts
for the calibrated source.

## Calibrated hashes

| File | SHA-256 |
|---|---|
| test_batch27_observation_audit.py | a844a1c42a98dc5f017d99cfbe8877aaa59a713d74c20be5010e5509a805e53f |
| test_batch27_daily_report_volume.py | d063e2e5f289da20e61ea54d9cee8472caf9467789b8bce85b33501ca01d2c62 |
| test_batch27_logging_volume.py | 93ebcc48d1b60f815cf621d55a7815831fe6f0a137d56e3faf24e00558381b0c |

The independent test author executed no tests; ROOT owns serial execution.

## Reset isolation cleanup calibration

The first candidate-only reset receipt reported one behavioral failure and
one teardown error. The behavioral failure was the intended native-channel
invalidation assertion. Its failure skipped the final DROP TRIGGER, so later
fixture Store.close tried to flush into the same deliberately failing SQL
trigger. This extra teardown error cannot be counted as a qualified clean red.

The original reset source was preserved byte-for-byte in
`docs/batch27_evidence/initial_specs/test_batch27_reset_isolation.py`
(SHA-256 `3fecebfbc60317a627d4fd4dfdc994bf13a70f4bf910e343e9cb1516c8424c14`).
Only the trigger lifetime was wrapped in try/finally with DROP TRIGGER IF
EXISTS. Native sent/ended invalidation, poison/stuck cleanup, generation hook,
and old-End rejection assertions are unchanged. Production was not modified.
ROOT must rerun this candidate-only case; the first receipt remains preserved.

## Owed-End bootstrap counter calibration

The original equivalence source (SHA-256
`6fa99a2bf52441c80ac8711917952a69ce0ddec2af02939c8c851af8aee5405d`)
was saved byte-for-byte in `initial_specs/test_batch27_observer_equivalence.py`.
Its owed-End case incorrectly assumed native sent/ended counters were zero
immediately after `_multi_native.connect`. Actual B25 native bootstrap already
completed one positions request and End. The original candidate and two
baseline receipts failed that setup assertion before reaching the statistics
assertion; they do not naturally prove a missing-failed-attempt summary bug.

Only this new test's assertions now measure sent/ended deltas relative to the
completed bootstrap. The timeout must add exactly one request and zero Ends;
its real owed End and the following request must yield exactly two new
requests and two new Ends. The actual two wire packets, exact original
exception object, pending Future, one drain, and statistics count=2 remain
unchanged. No native debt or counter is cleared. Bootstrap does not traverse
`_clean_request`, so it must not inflate these two measured query attempts.
ROOT must regenerate both isolated baseline receipts for this calibrated
source, and honestly classify subsequent candidate evidence by actual result.

## Diagnostic replay commission calibration

The first legacy-report repair run reported 89 passed / 1 failed / 0 errors.
All nine original regression cases passed. The new diagnostic-attachment
case's expected-two-row setup failed before report assertions: its replay
copied a historical FillEvent still carrying commission 1.00 after a genuine
late correction to 1.02. Store correctly processed the replayed 1.00 as another
commission correction, producing three diagnostic rows rather than two.
This is an unrelated fixture effect, not a report or commission-handling bug.

The original new guard was saved byte-for-byte as
`initial_specs/test_batch27_diagnostic_attachment.py` (SHA-256
`6a0e7935138c6544cb172f14b500fadfb4d57089c1a62d0c22080731cacb3c2c`).
Only the query-sourced replay now carries the latest known commission 1.02.
The expected real commission correction and time-review rows remain two;
unchanged ledger economics, current diagnostic-day visibility and exact
attachment audit identity/timestamp/raw-payload assertions are unchanged.
No production or pre-existing test was modified. The original receipt remains
preserved and ROOT must rerun this calibrated new guard serially.

## Batch10 successful QUERY log-level contract migration

The first full run reached the old two parametrizations of
`test_F5_query_elapsed_includes_old_end_drain`. Their expected INFO level
conflicts with the explicit Batch27 section 2.3 requirement that successful
QUERY details move to DEBUG. This is an intentional logging contract migration,
not a product failure, timing flake, hidden retry or waived broker assertion.

The entire pre-migration file was preserved byte-for-byte as
`initial_specs/test_batch10_pre_debug_contract.py`; its SHA-256 is
`5b875c8df97861e22427223e7065a74bddf04a5da1c7115b431871369d177c47` and matches the original preflight lock. Its content also equals
the batch27b Git version after newline normalization.
Only this function's caplog level and expected LogRecord level change from
INFO to DEBUG. Request/End/drain behavior, exactly one QUERY record, channel,
line shape and elapsed >=1.65 assertions are unchanged. Every byte outside the
function is unchanged; every other top-level AST node is identical. Adjacent
HISTORY and SLOW_TICK INFO contracts remain untouched. No production changed.

New file SHA-256: `e5004f67d963f2717d774ab8bd5204f9a87a381f274b9d66f4766daa42bec8c9`. Exact diff and static proof are in
`legacy_query_debug_contract_migration.diff/.json`. ROOT must perform serial
focused verification and restart the full acceptance sequence on this frozen
source; this author ran no tests.
