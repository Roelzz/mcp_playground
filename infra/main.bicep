@description('Name of the Azure Container App.')
param appName string = 'agent-playground'

@description('Azure region for all resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Public container image to deploy from GHCR, for example ghcr.io/owner/agent-integration-playground:latest.')
param containerImage string

@description('Bootstrap administrator username for the app.')
param bootstrapUser string = 'admin'

@secure()
@description('Bootstrap administrator password for the app. Supply at deployment time; do not store in parameters files.')
param bootstrapPassword string

@description('Container CPU allocation, for example 0.5.')
param cpu string = '0.5'

@description('Container memory allocation, for example 1Gi.')
param memory string = '1Gi'

@description('Azure Files share quota in GiB.')
param fileShareQuotaGb int = 5

@description('Application log level.')
param logLevel string = 'INFO'

var storageAccountName = take('st${uniqueString(resourceGroup().id)}', 24)
var fileShareName = 'playground-data'
var managedEnvironmentName = '${appName}-env'
var environmentStorageName = 'playground-data'
var volumeName = 'playground-data'
var publicBaseUrl = 'https://${appName}.${managedEnvironment.properties.defaultDomain}'

resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2024-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2024-01-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    shareQuota: fileShareQuotaGb
    enabledProtocols: 'SMB'
  }
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: managedEnvironmentName
  location: location
  // No appLogsConfiguration on purpose: omitting it keeps logs off and avoids Log Analytics cost.
  // The API rejects destination: 'none'; the property must be absent instead.
  properties: {}
}

resource environmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: managedEnvironment
  name: environmentStorageName
  properties: {
    azureFile: {
      accessMode: 'ReadWrite'
      accountName: storageAccount.name
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: fileShare.name
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 2009
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'bootstrap-password'
          value: bootstrapPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: containerImage
          env: [
            {
              name: 'HOST'
              value: '0.0.0.0'
            }
            {
              name: 'PORT'
              value: '2009'
            }
            {
              name: 'DB_PATH'
              value: '/data/playground.db'
            }
            // SQLite WAL mode needs mmap-backed shared memory; SMB does not provide that, so DELETE is required for Azure Files.
            {
              name: 'SQLITE_JOURNAL_MODE'
              value: 'DELETE'
            }
            {
              // Azure Files is an SMB/CIFS share where POSIX byte-range locks are
              // unreliable, making SQLite fail with "database is locked". The
              // unix-dotfile VFS uses a lock file instead and works over SMB.
              name: 'SQLITE_VFS'
              value: 'unix-dotfile'
            }
            {
              name: 'SQLITE_BUSY_TIMEOUT'
              value: '15000'
            }
            {
              name: 'PUBLIC_BASE_URL'
              value: publicBaseUrl
            }
            {
              name: 'INSECURE_COOKIES'
              value: '0'
            }
            {
              name: 'LOG_LEVEL'
              value: logLevel
            }
            {
              name: 'BOOTSTRAP_USER'
              value: bootstrapUser
            }
            {
              name: 'BOOTSTRAP_PASSWORD'
              secretRef: 'bootstrap-password'
            }
          ]
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 2009
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
              successThreshold: 1
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 2009
                scheme: 'HTTP'
              }
              initialDelaySeconds: 15
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
              successThreshold: 1
            }
          ]
          resources: {
            cpu: any(cpu)
            memory: memory
          }
          volumeMounts: [
            {
              volumeName: volumeName
              mountPath: '/data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        // Correctness constraint: SQLite cannot tolerate a second writer on a network share, so do not increase this.
        maxReplicas: 1
      }
      volumes: [
        {
          name: volumeName
          storageType: 'AzureFile'
          storageName: environmentStorage.name
        }
      ]
    }
  }
}

output fqdn string = '${appName}.${managedEnvironment.properties.defaultDomain}'
output publicBaseUrl string = publicBaseUrl
output storageAccountName string = storageAccount.name
output containerAppName string = containerApp.name
output resourceGroupName string = resourceGroup().name
