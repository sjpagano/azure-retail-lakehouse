# Governance, ownership, and data catalog

This is an implemented-control inventory and proposed operating model, **not** a
compliance certification. Only synthetic examples are approved for the demo.

## Accountabilities

The repository owner acts as data owner, steward, and platform maintainer for
this personal project. These are responsibilities, not invented employees.
In an organization, separate them:

| Responsibility | Accountable role | Evidence / approval |
|---|---|---|
| Approve purpose, consumers, retention | Data owner | Versioned governance changes |
| Investigate rejects and request corrections | Data steward | Batch ID, rule counts, incident record |
| Maintain transforms and infrastructure | Data engineer | Tests, reviewed commit, deployment record |
| Grant/revoke access and rotate secrets | Platform/security owner | RBAC review and Key Vault audit logs |
| Consume only committed, approved batches | Analyst | Manifest-aware query and reconciliation |

`ownerObjectId` is a resource tag for stewardship. It does not automatically
grant the supplied person access. Access must be assigned separately.

## Catalog and classification

| Zone / object | Contents | Classification if real | Retention target |
|---|---|---|---|
| bronze/*.csv | Original orders, including email | Restricted personal data | 30 days |
| silver/*.jsonl | Orders and deterministic customer token | Restricted pseudonymous data | 365 days |
| gold/*.jsonl | Date/region/currency totals | Internal, potentially sensitive small groups | 365 days |
| quality/*.json | Counts, lineage, fingerprints, business totals | Internal operational metadata | 365 days |
| quarantine/*.jsonl | Record ordinals and rule codes, no raw values | Internal operational metadata | 30 days |
| manifests/*.json | Committed-batch pointers and source checksum | Internal operational metadata | 365 days |

Bicep applies lifecycle rules by container prefix. Soft delete keeps lake blobs
recoverable for a further seven days after deletion; deletion is asynchronous,
so retention targets are not exact erasure deadlines. Azure diagnostics retain
30 days in Log Analytics. Key Vault soft delete is seven days. Host/package
storage is separate and has no automatic retention policy; retire old deployment
packages after rollback needs are satisfied. Local files have **no** automatic
retention enforcement; delete local demo outputs manually when no longer needed.

## Implemented access boundaries

| Principal | Grants defined by Bicep | Not granted |
|---|---|---|
| Function user-assigned identity | Bronze Blob Data Reader; each output container Blob Data Contributor | Bronze write; broad subscription roles |
| Function identity | Host storage Blob Data Owner and Queue Data Contributor | Access to other applications' host storage |
| Function identity | Secrets User on worker vault | Invocation-vault secret access |
| Data Factory system identity | Secrets User on separate invocation vault | Lake access; customer HMAC key |
| Human owner/analyst/uploader | None automatically | All lake access until explicitly approved |

Storage disables anonymous blob and shared-key access. SDK access uses managed
identity. TLS is required. Function invocation uses a function key stored in the
invocation vault; the HTTP handler is not anonymous. The linked service's
`authentication: Anonymous` denotes no App Service/Entra authentication layer;
the separate Function key still authenticates requests. This is a documented
tradeoff; an Entra-protected endpoint is a production hardening option.

Output contributor roles include delete/overwrite capability even though the
application refuses overwrites. This is not storage-level immutable retention.
Deployers need elevated provisioning permissions; use separate runtime identities
and time-limited deployment grants. Do not give analysts subscription Contributor.

## Privacy and secrets

Email is normalized and HMAC-SHA256 pseudonymized with a >=32-character random
secret. Ordinary SHA-256 of email would permit easy dictionary attacks; a secret
key reduces that risk. Deterministic tokens remain linkable and are **not anonymous**.
Keep silver restricted. Gold has no minimum group-size suppression in this demo.

Two vaults prevent Data Factory from reading the HMAC secret. The Function's
Key Vault reference pins an explicit secret version for reproducibility. Rotation
requires updating the reference, testing, and deliberately planning reprocessing:
new keys produce new tokens and batch IDs. Do not merge old/new token populations
as if identifiers were unchanged. Function keys can be rotated independently;
update the invocation-vault secret, validate ADF, then retire the old key.

Never commit real CSVs, `.env`, local Function settings, compiled deployment
parameters, credentials, or unsanitized diagnostic exports. The demo key in
`scripts/demo.py` is intentionally public and must not be used for real data.

## Lineage, change control, and incident handling

The report links an exact input checksum and logical path to silver, gold, and
manifest paths. Batch identity also changes with policy, transform, key, business
date, or source name. Reports survive bronze retention, so checksum lineage can
remain after the original raw object expires; that is not full replay capability.
There is no Purview catalog integration.

Contract changes require a version bump, test fixtures, updated field catalog,
and consumer-impact notes. Corrected inputs get **new source names**. A steward
must mark superseded batches in the downstream consumer's selection logic to
avoid double counting; this repo does not maintain a global batch registry.

On rejection: retain the report, investigate restricted bronze, fix the source,
publish a new batch, reconcile, and record disposition. Do not edit curated data
or relax thresholds to make a run green. On suspected disclosure: revoke access,
rotate affected credentials, preserve access logs, and follow the organization's
incident policy. Subject-access/deletion workflows are not implemented.

## Production readiness gaps

Private endpoints/firewalls, Entra invocation auth, Purview discovery, protected
branches and reviewers, cross-batch deduplication, alert rules, key-rotation
automation, deletion/legal-hold workflows, dependency lockfiles, and load tests
remain future work. Public endpoints still require authentication, but no
network perimeter is claimed. Compile validation does not prove a working Azure
deployment; complete the runbook's cloud acceptance checks before claiming one.

References: [Key Vault RBAC](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide),
[storage lifecycle management](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-overview),
[blob soft delete](https://learn.microsoft.com/en-us/azure/storage/blobs/soft-delete-blob-overview).
