targetScope = 'resourceGroup'

@description('Lowercase prefix; resource names also include a stable unique suffix.')
@minLength(3)
@maxLength(6)
param prefix string = 'retail'
param location string = resourceGroup().location
@description('Principal object ID for the portfolio owner (documented stewardship, no automatic grants).')
param ownerObjectId string
@secure()
@minLength(32)
param pseudonymKey string

var suffix = uniqueString(resourceGroup().id)
var tags = { project: 'retail-lakehouse', environment: 'portfolio', dataClassification: 'synthetic-demo', owner: ownerObjectId }
var blobReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var blobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var blobOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var queueContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
var secretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var zones = ['bronze', 'silver', 'gold', 'quality', 'quarantine', 'manifests']

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-worker-${suffix}'
  location: location
  tags: tags
}
resource lake 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}lake${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}
resource lakeBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: lake
  name: 'default'
  properties: { deleteRetentionPolicy: { enabled: true, days: 7 } }
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for zone in zones: {
  parent: lakeBlobs
  name: zone
  properties: { publicAccess: 'None' }
}]
resource lakeAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (zone, i) in zones: {
  name: guid(containers[i].id, identity.id, zone)
  scope: containers[i]
  properties: {
    roleDefinitionId: zone == 'bronze' ? blobReader : blobContributor
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}]
resource retention 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: lake
  name: 'default'
  properties: {
    policy: {
      rules: [for zone in zones: {
        name: 'retain-${zone}'
        enabled: true
        type: 'Lifecycle'
        definition: {
          filters: { blobTypes: ['blockBlob'], prefixMatch: ['${zone}/'] }
          actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: contains(['bronze', 'quarantine'], zone) ? 30 : 365 } } }
        }
      }]
    }
  }
}
resource hostStore 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}host${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { allowBlobPublicAccess: false, allowSharedKeyAccess: false, supportsHttpsTrafficOnly: true, minimumTlsVersion: 'TLS1_2' }
}
resource hostBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: hostStore
  name: 'default'
}
resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: hostBlobs
  name: 'packages'
  properties: { publicAccess: 'None' }
}
resource hostBlobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(hostStore.id, identity.id, blobOwner)
  scope: hostStore
  properties: { principalId: identity.properties.principalId, roleDefinitionId: blobOwner, principalType: 'ServicePrincipal' }
}
resource hostQueueAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(hostStore.id, identity.id, queueContributor)
  scope: hostStore
  properties: { principalId: identity.properties.principalId, roleDefinitionId: queueContributor, principalType: 'ServicePrincipal' }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}kv${suffix}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    accessPolicies: []
  }
}
resource pseudonymSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'pseudonym-key'
  properties: { value: pseudonymKey }
}
resource workerVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, secretsUser)
  scope: vault
  properties: { principalId: identity.properties.principalId, roleDefinitionId: secretsUser, principalType: 'ServicePrincipal' }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-insights-${suffix}'
  location: location
  kind: 'web'
  tags: tags
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: '${prefix}-plan-${suffix}'
  location: location
  kind: 'functionapp'
  tags: tags
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}
resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: '${prefix}-quality-${suffix}'
  location: location
  kind: 'functionapp,linux'
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: identity.id
    functionAppConfig: {
      runtime: { name: 'python', version: '3.12' }
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${hostStore.properties.primaryEndpoints.blob}${packages.name}'
          authentication: { type: 'UserAssignedIdentity', userAssignedIdentityResourceId: identity.id }
        }
      }
      scaleAndConcurrency: { maximumInstanceCount: 40, instanceMemoryMB: 2048 }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: hostStore.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: identity.properties.clientId }
        { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        { name: 'LAKE_ACCOUNT_URL', value: lake.properties.primaryEndpoints.blob }
        { name: 'PSEUDONYM_KEY', value: '@Microsoft.KeyVault(SecretUri=${pseudonymSecret.properties.secretUriWithVersion})' }
      ]
    }
  }
  dependsOn: [hostBlobAccess, hostQueueAccess, workerVaultAccess, lakeAccess]
}
resource factory 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: '${prefix}-adf-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {}
}
resource factoryVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(invocationVault.id, factory.id, secretsUser)
  scope: invocationVault
  properties: { principalId: factory.identity.principalId, roleDefinitionId: secretsUser, principalType: 'ServicePrincipal' }
}
// Separate trust boundary: Data Factory may invoke the worker, but cannot read its HMAC key.
resource invocationVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}akv${suffix}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    accessPolicies: []
  }
}
resource kvLink 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: factory
  name: 'GovernanceVault'
  properties: { type: 'AzureKeyVault', typeProperties: { baseUrl: invocationVault.properties.vaultUri } }
}
resource functionLink 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: factory
  name: 'QualityFunction'
  properties: {
    type: 'AzureFunction'
    typeProperties: {
      functionAppUrl: 'https://${functionApp.properties.defaultHostName}'
      authentication: 'Anonymous'
      functionKey: { type: 'AzureKeyVaultSecret', secretName: 'quality-function-key', store: { referenceName: kvLink.name, type: 'LinkedServiceReference' } }
    }
  }
}
resource pipeline 'Microsoft.DataFactory/factories/pipelines@2018-06-01' = {
  parent: factory
  name: 'ValidateAndPublishSales'
  properties: {
    description: 'Validate one immutable bronze batch; fail the run when the data quality gate rejects it.'
    concurrency: 1
    parameters: { blob_name: { type: 'String' }, as_of: { type: 'String' } }
    activities: [
      {
        name: 'ValidateAndPublish'
        type: 'AzureFunctionActivity'
        linkedServiceName: { referenceName: functionLink.name, type: 'LinkedServiceReference' }
        policy: { timeout: '0.00:04:00', retry: 2, retryIntervalInSeconds: 30, secureInput: true, secureOutput: true }
        typeProperties: {
          functionName: 'process-sales'
          method: 'POST'
          headers: { 'Content-Type': 'application/json' }
          body: { value: '@setProperty(setProperty(json(\'{}\'), \'blob_name\', pipeline().parameters.blob_name), \'as_of\', pipeline().parameters.as_of)', type: 'Expression' }
        }
      }
      {
        name: 'EnforceQualityGate'
        type: 'IfCondition'
        dependsOn: [{ activity: 'ValidateAndPublish', dependencyConditions: ['Succeeded'] }]
        typeProperties: {
          expression: { value: '@equals(activity(\'ValidateAndPublish\').output.status, \'passed\')', type: 'Expression' }
          ifTrueActivities: []
          ifFalseActivities: [{ name: 'RejectBatch', type: 'Fail', typeProperties: { message: 'Data quality gate rejected this batch. Review the quality report and quarantine row references.', errorCode: 'DQ_GATE_REJECTED' } }]
        }
      }
    ]
  }
}
resource factoryDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'pipeline-audit'
  scope: factory
  properties: { workspaceId: logs.id, logs: [{ category: 'PipelineRuns', enabled: true }, { category: 'ActivityRuns', enabled: true }] }
}
resource vaultDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'secret-audit'
  scope: vault
  properties: { workspaceId: logs.id, logs: [{ category: 'AuditEvent', enabled: true }] }
}
resource invocationVaultDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'invocation-audit'
  scope: invocationVault
  properties: { workspaceId: logs.id, logs: [{ category: 'AuditEvent', enabled: true }] }
}
resource lakeDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'lake-audit'
  scope: lakeBlobs
  properties: { workspaceId: logs.id, logs: [{ category: 'StorageRead', enabled: true }, { category: 'StorageWrite', enabled: true }, { category: 'StorageDelete', enabled: true }] }
}

output functionAppName string = functionApp.name
output factoryName string = factory.name
output lakeName string = lake.name
output keyVaultName string = vault.name
output invocationVaultName string = invocationVault.name
output pipelineName string = pipeline.name
output functionUrl string = 'https://${functionApp.properties.defaultHostName}/api/process-sales'
