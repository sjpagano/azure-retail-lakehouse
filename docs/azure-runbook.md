# Azure deployment and operations runbook

Status: **deployed and end-to-end smoke-tested by the repository owner on
October 7, 2026**. The [deployment evidence](../evidence/azure-deployment.md)
records the tested commit, run IDs, six screenshots, quality decisions, retry
publication counts, and revenue reconciliation. The runbook below reflects the
dedicated `adf` Function-key bootstrap used in that walkthrough. Additional
security and operational checks listed here were not all captured in the smoke
test. Tenant policy, regional capacity, and RBAC propagation can still affect
new deployments; do not treat historical success as current availability.

## Prerequisites and budget

- An Azure subscription and a dedicated, disposable resource group.
- A current Azure CLI with Bicep and Data Factory extension (`az extension add
  --name datafactory`), Python 3.12, and Bash/Cloud Shell for these commands.
- Provisioning rights plus permission to create role assignments (for example,
  Contributor + Role Based Access Control Administrator at the intended scope).
- A region supporting Functions Flex Consumption and Data Factory; check
  `az functionapp list-flexconsumption-locations -o table`.
- Register Microsoft.Web, Storage, DataFactory, ManagedIdentity, KeyVault,
  Insights, and OperationalInsights providers if the subscription requires it.

No cost ceiling is enforced by the template. Create an Azure Cost Management
budget and alert for the resource group first. Functions, ADF runs, storage,
Key Vault operations, and log ingestion can incur charges. No scheduled trigger
or always-ready Function instance is configured. Do not deploy into a shared
production resource group or use real customer data for this exercise.

## 1. Provision after reviewing what-if

From the repo root in Bash, choose your own subscription and region:

```bash
az login
az account set --subscription '<your-subscription-id>'
export RETAIL_RG='retail-portfolio-demo'
export AZURE_LOCATION='<supported-region>'
export RETAIL_PREFIX='retail'  # 3–6 lowercase letters/digits, first character a letter
export OWNER_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"
export PSEUDONYM_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
az group create --name "$RETAIL_RG" --location "$AZURE_LOCATION" --output none
az deployment group what-if --resource-group "$RETAIL_RG" --parameters infra/main.bicepparam
# Review planned resources and role scopes before the next command.
az deployment group create --name retail --resource-group "$RETAIL_RG" \
  --parameters infra/main.bicepparam --output none
unset PSEUDONYM_KEY
```

Do not echo the key, enable shell tracing, or check in compiled parameter files.
The Bicep parameter is marked secure and read from the environment. The random-key
step is for a **new demo deployment only**: recreating it during an update rotates
customer tokens. For subsequent deployments securely retrieve the existing key
with an approved operator identity, or perform the documented rotation procedure.
If deployment fails before `unset`, remove the environment variable yourself.
Service principals need their own object ID instead of `signed-in-user`.

Read nonsensitive deployment outputs:

```bash
APP=$(az deployment group show -g "$RETAIL_RG" -n retail --query properties.outputs.functionAppName.value -o tsv)
ADF=$(az deployment group show -g "$RETAIL_RG" -n retail --query properties.outputs.factoryName.value -o tsv)
LAKE=$(az deployment group show -g "$RETAIL_RG" -n retail --query properties.outputs.lakeName.value -o tsv)
CALL_VAULT=$(az deployment group show -g "$RETAIL_RG" -n retail --query properties.outputs.invocationVaultName.value -o tsv)
```

## 2. Deploy the Python Function

```bash
python scripts/package_function.py
az functionapp deployment source config-zip --resource-group "$RETAIL_RG" \
  --name "$APP" --src dist/function.zip --build-remote true --output none
az functionapp function list -g "$RETAIL_RG" -n "$APP" --query '[].name' -o table
```

Confirm `process_sales` is registered; its route is `/api/process-sales`. The ZIP
contains source and requirements, not Windows-built Python packages. Azure builds
Linux dependencies. If no functions appear, inspect deployment/build logs and
Key Vault reference health before triggering ADF. Managed identity assignments
can take time to propagate.

## 3. Bootstrap invocation key and uploader access

ADF needs a Function key created after code deployment.
The following explicit human grants are **not** part of the runtime template.
Have an authorized administrator approve/create them at these narrow scopes:

```bash
LAKE_ID=$(az storage account show -g "$RETAIL_RG" -n "$LAKE" --query id -o tsv)
CALL_VAULT_ID=$(az keyvault show -g "$RETAIL_RG" -n "$CALL_VAULT" --query id -o tsv)
az role assignment create --assignee-object-id "$OWNER_OBJECT_ID" --assignee-principal-type User \
  --role 'Storage Blob Data Contributor' --scope "$LAKE_ID/blobServices/default/containers/bronze" --output none
az role assignment create --assignee-object-id "$OWNER_OBJECT_ID" --assignee-principal-type User \
  --role 'Storage Blob Data Reader' --scope "$LAKE_ID" --output none
az role assignment create --assignee-object-id "$OWNER_OBJECT_ID" --assignee-principal-type User \
  --role 'Key Vault Secrets Officer' --scope "$CALL_VAULT_ID" --output none
```

The account-scoped Reader grant is for inspecting this synthetic demo's lake;
use narrower container scopes when required by your access policy. Wait for RBAC
to propagate. Create a dedicated function-level `adf` key and transfer it without
printing it or placing its value in process arguments:

```bash
(
  set -euo pipefail
  set +x
  umask 077
  az functionapp function keys set -g "$RETAIL_RG" -n "$APP" \
    --function-name process_sales --key-name adf --output none
  KEY_FILE=$(mktemp)
  trap 'rm -f -- "$KEY_FILE"' EXIT
  az functionapp function keys list -g "$RETAIL_RG" -n "$APP" \
    --function-name process_sales --query adf -o tsv | tr -d '\r\n' > "$KEY_FILE"
  test -s "$KEY_FILE" || { echo 'No function key returned'; exit 1; }
  az keyvault secret set --vault-name "$CALL_VAULT" --name quality-function-key \
    --file "$KEY_FILE" --output none
)
```

Run these steps in a trusted shell with tracing disabled. Remove temporary human
grants after the demo if they are no longer needed. The Function key is kept in a
separate vault from the pseudonym key; ADF cannot read the latter.
This is an initial bootstrap: rerunning `keys set` without a supplied value can
rotate the named key, so coordinate the vault update rather than rerunning it
casually during active use.

## 4. Run clean and rejected cloud acceptance scenarios

```bash
az storage blob upload --account-name "$LAKE" --container-name bronze \
  --name demo/sales-clean.csv --file examples/sales-clean.csv --auth-mode login --overwrite false --output none
RUN_ID=$(az datafactory pipeline create-run -g "$RETAIL_RG" --factory-name "$ADF" \
  --name ValidateAndPublishSales --parameters '{"blob_name":"demo/sales-clean.csv","as_of":"2026-09-30"}' --query runId -o tsv)
az datafactory pipeline-run show -g "$RETAIL_RG" --factory-name "$ADF" --run-id "$RUN_ID" --query status -o tsv
```

Wait for a terminal status via ADF Monitor. Clean should succeed. Repeat with
`demo/sales-dirty.csv` and the dirty fixture; it should fail with
`DQ_GATE_REJECTED`, not an infrastructure error. Retry the identical clean run:
there should still be exactly one manifest for that source/context.

For output inspection, obtain approved read access to the output containers
(the bootstrap above includes a temporary demo Reader grant). Core functional
checks, captured in the [October 7 evidence](../evidence/azure-deployment.md):

- Clean: six accepted, zero rejected; gold USD4899/EUR3500/GBP1500 cents.
- Dirty: one valid row, five rejected; `DQ_GATE_REJECTED` after successful evaluation.
- Retry: succeeded; final counts are two quality reports and one file each in
  manifests, silver, and gold for these fixtures.

Additional checks before a broader operational/security validation claim
(not established by the six captured screenshots):

- Inspect rejected-row quarantine references and manifest-to-source contents.
- No raw email in silver, gold, reports, quarantine, or application log messages.
- Lake anonymous access fails; ADF cannot retrieve the worker HMAC secret.
- The Function's managed identity reads bronze but cannot write to it.
- A report checksum matches the uploaded source; output paths are reachable.

The cloud source path includes `demo/`, so its batch ID differs from the local
fixture path. Save sanitized run IDs, screenshots, commit SHA, and outcomes in
an evidence note. Do not publish raw run outputs containing credentials.

## Record acceptance evidence

Use [evidence/azure-deployment.md](../evidence/azure-deployment.md) as the format:
record the tested commit, capture date, scenario-to-run-ID mapping, expected and
observed results, source screenshots, and any unverified checks. Keep local demo
artifacts separate from cloud results. Confirm totals from the actual gold blob,
not just the quality report. Container counts demonstrate the observed final
publication count, not byte-for-byte immutability or global exactly-once delivery.
After teardown, record that the resources were removed; do not infer cleanup
from the age of a screenshot.

## Operations and recovery

| Symptom | Investigate | Recovery |
|---|---|---|
| DQ_GATE_REJECTED | Quality report, quarantine ordinals, restricted bronze | Correct source under a new immutable name; do not relax policy |
| Function 400 | Parameter shape/path/business date | Fix ADF parameters |
| Function 500 | Bronze availability/size, RBAC, Key Vault resolution, app settings | Fix cause, retry identical context |
| Function 401/403 | Invocation key, linked service, vault access | Repair/rotate key and retry |
| Missing manifest | Failure before final publication | Retry identical context; consumers ignore partial files |
| Conflicting existing object | Manual mutation or unversioned code change | Investigate; do not overwrite blindly |
| Timeout | File size, dependency/network delays | Stay within demo limits; redesign large jobs asynchronously |

ADF/Key Vault/storage diagnostics go to Log Analytics. Application Insights
records structured batch status messages without raw records. Alerts are not
provisioned: configure failed-run and availability alerts before unattended use.
`secureInput`/`secureOutput` limits activity payload visibility; it does not replace
log-access control. Generic processing errors intentionally omit exception text.

## Cleanup

Stop activity and export only sanitized evidence. In Azure Portal, inspect the
dedicated resource group and verify it contains **only this demo**, then delete
that resource group. This destroys its stored data and services; do not delete a
shared group. Key Vault soft-deleted objects may persist for seven days. Review
remaining role assignments, retained resources, and Cost Management afterward.

References: [Functions Flex deployment](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to),
[Bicep parameter files](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/parameter-files),
[ADF Function activity](https://learn.microsoft.com/en-us/azure/data-factory/control-flow-azure-function-activity).
