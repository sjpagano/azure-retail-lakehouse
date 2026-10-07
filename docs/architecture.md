# Design decisions and failure semantics

## One engine, two adapters

`quality.evaluate` is pure: bytes + business date + key -> report and rows.
`runner.run` adds lineage and publishes through a minimal `put` protocol.
`LocalStore` enables a subscription-free demo; `AzureStore` uses the Blob SDK and
`DefaultAzureCredential`. `function_app.py` is a thin HTTP boundary. This keeps
cloud calls mockable without mocking the business rules being tested.

ADF is the orchestrator, not the transformation engine. It retries transient
Function failures twice and explicitly fails quality-rejected runs. A rejected
quality decision returns HTTP 200 with `status: rejected`, because evaluation
succeeded; the downstream ADF `Fail` activity gives the correct pipeline outcome.
There are no automatic triggers, so deployment does not start recurring work.

## Publication protocol

1. Read a bounded bronze object. Azure download is conditional on its observed
   ETag, so concurrent replacement fails rather than processing an unpinned read.
2. Parse and evaluate quality. Hash the exact bytes and transformation context.
3. Write quarantine and report using create-only semantics.
4. If passed, write silver and gold.
5. Write the manifest **last**, as the publication commit marker.

An existing object is accepted only when its bytes exactly match the intended
write; otherwise the run fails. Retrying identical input is safe. A failure
between objects can leave orphaned silver/gold files but no manifest. Consumers
following manifests do not see those partial batches. The report's intended
output paths are not proof of a successful commit; the manifest is.

Local exclusive file creation is not crash-atomic within a single file write.
An interrupted write can leave a partial local file, and retry deliberately
refuses to overwrite it. Recover by investigating the failed output and rerunning
in a new empty local output directory. Azure block-blob upload commits a whole
object, but full cloud failure behavior still requires integration testing.

Neither adapter protects against a privileged administrator changing/deleting
objects later. There is no multi-object database transaction, exactly-once event
delivery, storage WORM lock, or global deduplication service. Identical order IDs
in different source batches are allowed and must not be blindly summed.

## Deliberate scale boundary

The Function reads one file into memory with a 10 MiB / 50,000-record cap. The
three-minute Function timeout stays below the Azure Function activity's practical
HTTP response limit. ADF concurrency is one to keep the demo easy to inspect;
content-addressed outputs support retries, not unlimited workload guarantees.
Large files should move to streaming/partitioned processing or a Spark engine,
with orchestration using an asynchronous job protocol.

## Alternatives

- Databricks: useful for distributed processing, unnecessary for this bounded demo.
- Data Factory Mapping Data Flows: fewer code files, but harder to reproduce the
  exact quality engine without a subscription.
- Plain email hashes: rejected because likely emails are guessable; use keyed HMAC.
- Overwrite-in-place outputs: rejected because they hide lineage and break retries.
- Reject-on-first-bad-row: simpler but loses the useful multi-rule quality report.

References: [Azure Function activity](https://learn.microsoft.com/en-us/azure/data-factory/control-flow-azure-function-activity),
[Functions identity-based storage](https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference#connecting-to-host-storage-with-an-identity).
