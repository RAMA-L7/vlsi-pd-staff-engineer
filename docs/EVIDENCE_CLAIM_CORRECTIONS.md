# Evidence-claim corrections (Conditional Findings Closure Sprint)

Prior reports and review evidence are preserved unchanged. This document supersedes overstated wording in earlier sprint reports; it does not rewrite those historical artifacts.

## Corrected retention claim

Replace unqualified phrases such as "no lost evidence" or "0 lost evidence" with:

> No durable qualified PASS under pre-boundary cancellation; no log-record loss on the normal serialized path (`stress_repeat`: 48 writes, 8 records/read-back per rep). Concurrent writers sharing one workspace lose ECO-log records (12/12, S3 section 5.2; unsupported single-writer precondition); post-boundary cancellation can leave a non-authorizing contradictory PASS (12/12); orphan temps occur at pre-replace crash boundaries.

Guaranteed/tested: single-writer serialized contract; pre-boundary cancellation (60/60); fsync + atomic `os.replace` (0 torn JSON in 246 repetitions); 3-retry correctness; lock recovery 12/12.
Not guaranteed / not tested: concurrent-writer safety; post-boundary record consistency; directory fsync; whole-file rollback detection; a second host; native POSIX execution; real-tool EDA on the fixed candidate.

See `R03_EVIDENCE_CLAIM_AUDIT.md` in the sprint reports for the full claim-by-claim audit.
