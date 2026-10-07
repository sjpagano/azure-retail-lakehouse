using './main.bicep'

param prefix = readEnvironmentVariable('RETAIL_PREFIX', 'retail')
param location = readEnvironmentVariable('AZURE_LOCATION')
param ownerObjectId = readEnvironmentVariable('OWNER_OBJECT_ID')
// The secret is supplied only at deployment time; never commit compiled parameters.
param pseudonymKey = readEnvironmentVariable('PSEUDONYM_KEY')
