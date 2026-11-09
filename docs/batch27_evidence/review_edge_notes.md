# Batch27 independent review edge findings

The source writer is /root/b27a_implement; /root/b27a_review reviews read-only; /root/b27a_tests owns only new tests; ROOT runs all verification serially.

- The 990-case legacy regression receipt `focused_previous_batches` preserved 9 real deterministic failures: seven budget-sufficient broker-reason truncations, a prior-day execution shown in the receiving-day report, and a shutdown-report render skipped before failed attachment-directory I/O. Old tests were not edited.
- Removed the arbitrary per-event 2048-byte cap. Fully preserve important diagnostics when the total post-redaction budget permits; only actual overflow goes to the complete sidecar.
- Main ORDER_PRICE_OBSERVATION and OBSERVATION_SUMMARY remain statistics only. Independently reviewed static omission: a PROTECT diagnostic ORDER need not have a TP_EVIDENCE duplicate. The complete sidecar uses a separate filter that includes every original diagnostic ORDER row, including invalid JSON conservatively.
- Ledger-day filtering is limited to FILL, PROTECT_FILL and PROTECT_RETIRED_FILL when a corresponding persisted fill establishes a different day. Unknown executions remain visible. Today commission corrections, time-review diagnostics and anomalies remain today facts even if their referenced fill is older.
- Rendering happens once before artifact directory/file I/O; the writer then exports the actual sidecar and replaces only reserved metadata. The original shutdown failure seam and safe order cleanup remain.
- The two additional diagnostic-sidecar/correction tests follow static fixes. They are passing-control guards, not claimed as previously observed product reds. Their query-sourced fill replay tests the real Store entrance, not a native reqExecutions/Future/End completion proof.
- The first new correction fixture inadvertently replayed its old commission after the latest correction and therefore legitimately created a second correction. Original spec and receipt are preserved; only replay commission was calibrated to the current 1.02. Independent review confirmed all strong assertions unchanged.
- Independent byte comparison found the other 138 original v6lite/scripts files unchanged, including all old tests, configuration, risk and untouched order/protection/evidence paths.

## First full-run contract migration

The complete first full run had1946 cases,1943 passed,2 failed,1 skipped,0 errors. Both failures were the old batch10 successful-QUERY INFO expectation, explicitly replaced by batch27 section2.3 DEBUG. This is neither a product regression nor a flake. The one old test function changes exactly two logging-level tokens; all unique-line, native drain/End, request and elapsed>=1.65s assertions remain. `legacy_query_contract.py` requires the full current file to equal the baseline bytes with exactly those substitutions and verifies the preserved original copy. All other137 original files outside the six production edits remain unchanged. The failed full run and original lock/review/compile proofs remain; final full3 restarts from run1.
