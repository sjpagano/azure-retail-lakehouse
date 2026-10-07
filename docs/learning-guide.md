# Turn the project into skills you can demonstrate

## A five-minute walkthrough

1. Run `python scripts/demo.py`. Explain why the dirty batch publishes no gold
   even though one record is valid.
2. Open the clean report and follow input checksum -> batch ID -> gold -> manifest.
3. Show the duplicate-order test and explain why both duplicates are rejected.
4. Show the simulated interrupted publication test. Explain why consumers use
   manifests instead of listing every gold object.
5. Walk through the RBAC matrix and the two vaults. Explain which parts are
   implemented locally, compile-validated, exercised in the
   [Azure smoke test](../evidence/azure-deployment.md), or still cloud-unverified.

## Exercises to implement yourself

- Add CAD currency support with a version bump, tests, and documented consumer impact.
- Add an approved-batch registry that records superseded corrections; prove that
  reprocessing cannot double-count a business order across batches.
- Add a latency check using an ingestion timestamp distinct from `as_of`.
- Add a manifest-aware SQL/DuckDB reader and prove per-currency reconciliation.
- Reproduce the documented deployment in a disposable Azure resource group and
  add cloud access-denial/privacy checks to the existing clean/failing/retry
  acceptance evidence. Measure actual cost before proposing a schedule.
- Add alerts and an incident drill, then implement secret rotation with tests.

## Interview questions

Why use HMAC rather than an unsalted hash? Does tokenization make data anonymous?
Why reject all duplicates instead of choosing the first? What happens if a process
dies while publishing? Where does this design stop being exactly-once? How does
an analyst avoid counting a corrected batch twice? What does Bicep compilation
prove, and what can only a cloud integration test prove?

## Honest resume wording

After running and understanding the project:

> Built a Python retail batch pipeline with schema and row-level quality gates,
> HMAC pseudonymization, quarantine reports, content-addressed publication, and
> 54 automated tests; deployed and smoke-tested Azure Data Factory/Functions/ADLS
> infrastructure, verified quality rejection, retry publication counts, and
> per-currency revenue reconciliation, and documented governance and recovery.

The deployment wording is backed by the owner's
[October 7, 2026 evidence](../evidence/azure-deployment.md), which identifies the
tested commit and the limits of the checks. Do not claim production traffic,
reduced costs, regulatory compliance, or a revenue impact without measured evidence. Use the
project to learn and modify the design, not just to repeat a generated explanation.
