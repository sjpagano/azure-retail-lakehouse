# Azure deployment and operations runbook

Status: Bicep compilation and local engine tests are verified. **These cloud
steps have not been executed for this project.** Tenant policy, regional capacity,
provider support, and RBAC propagation can affect deployment. Record a successful
cloud acceptance run before presenting the project as deployed.

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

ADF needs the Function's generated key, available only after code deployment.
The following explicit human grants are **not** part of the runtime template.
Have an authorized administrator approve/create them at these narrow scopes:

```bash
LAKE_ID=$(az storage account show -g "$RETAIL_RG" -n "$LAKE" --query id -o tsv)
CALL_VAULT_ID=$(az keyvault show -g "$RETAIL_RG" -n "$CALL_VAULT" --query id -o tsv)
az role assignment create --assignee-object-id "$OWNER_OBJECT_ID" --assignee-principal-type User \
  --role 'Storage Blob Data Contributor' --scope "$LAKE_ID/blobServices/default/containers/bronze" --output none
az role assignment create --assignee-object-id "$OWNER_OBJECT_ID" --assignee-principal-type User \
  --role 'Key Vault Secrets Officer' --scope "$CALL_VAULT_ID" --output none
```

Wait for RBAC to propagate. Transfer the function-level default key without
printing it or placing its value in process arguments:

```bash
umask 077
KEY_FILE=$(mktemp)
trap 'rm -f -- "$KEY_FILE"' EXIT
az functionapp function keys list -g "$RETAIL_RG" -n "$APP" \
  --function-name process_sales --query default -o tsv | tr -d '\r\n' > "$KEY_FILE"
test -s "$KEY_FILE" || { echo 'No function key returned'; exit 1; }
az keyvault secret set --vault-name "$CALL_VAULT" --name quality-function-key \
  --file "$KEY_FILE" --output none
rm -f -- "$KEY_FILE"
trap - EXIT
```

Run these steps in a trusted shell with tracing disabled. Remove temporary human
grants after the demo if they are no longer needed. The Function key is kept in a
separate vault from the pseudonym key; ADF cannot read the latter.

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
(for this synthetic demo only, an account-scoped Blob Data Reader grant is a
convenient temporary option). Confirm:

- Clean: six accepted, zero rejected; gold USD4899/EUR3500/GBP1500 cents.
- Dirty: one accepted, five rejected; report and quarantine but no manifest.
- No raw email in silver, gold, reports, quarantine, or application log messages.
- Lake anonymous access fails; ADF cannot retrieve the worker HMAC secret.
- The Function's managed identity reads bronze but cannot write to it.
- A report checksum matches the uploaded source; output paths are reachable.

The cloud source path includes `demo/`, so its batch ID differs from the local
fixture path. Save sanitized run IDs, screenshots, commit SHA, and outcomes in
an evidence note. Do not publish raw run outputs containing credentials.

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
