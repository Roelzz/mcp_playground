#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./deploy.sh [--yes]

Environment overrides:
  RESOURCE_GROUP       Azure resource group name (default: rg-agent-playground)
  LOCATION             Azure region (default: westeurope)
  BOOTSTRAP_PASSWORD   Required bootstrap admin password; prompted when interactive
  CONTAINER_IMAGE      Optional override for infra/main.parameters.json containerImage
  APP_NAME             Optional override for infra/main.parameters.json appName
  DEPLOYMENT_NAME      Optional deployment name (default: agent-playground)
  SKIP_CONFIRM=1       Skip subscription confirmation

Do not put BOOTSTRAP_PASSWORD in infra/main.parameters.json or commit it anywhere.
USAGE
}

yes_flag=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)
      yes_flag=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is not installed. Install it: https://learn.microsoft.com/cli/azure/install-azure-cli" >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Azure CLI is not logged in. Run: az login" >&2
  exit 1
fi

subscription_name="$(az account show --query name -o tsv)"
subscription_id="$(az account show --query id -o tsv)"
echo "Selected Azure subscription: ${subscription_name} (${subscription_id})"

if [[ "${SKIP_CONFIRM:-0}" != "1" && "$yes_flag" != "1" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Refusing to continue without confirmation on non-interactive stdin. Re-run with --yes or SKIP_CONFIRM=1." >&2
    exit 1
  fi
  read -r -p "Deploy to this subscription? Type 'yes' to continue: " confirmation
  if [[ "$confirmation" != "yes" ]]; then
    echo "Deployment cancelled."
    exit 0
  fi
fi

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-agent-playground}"
LOCATION="${LOCATION:-westeurope}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-agent-playground}"

if [[ -z "${BOOTSTRAP_PASSWORD:-}" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "Bootstrap admin password: " BOOTSTRAP_PASSWORD
    echo
  else
    echo "BOOTSTRAP_PASSWORD is required. Export it or run interactively to be prompted." >&2
    exit 1
  fi
fi

if [[ -z "$BOOTSTRAP_PASSWORD" ]]; then
  echo "BOOTSTRAP_PASSWORD cannot be empty." >&2
  exit 1
fi

az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

parameters=(
  @infra/main.parameters.json
  "location=${LOCATION}"
  "bootstrapPassword=${BOOTSTRAP_PASSWORD}"
)

if [[ -n "${CONTAINER_IMAGE:-}" ]]; then
  parameters+=("containerImage=${CONTAINER_IMAGE}")
fi

if [[ -n "${APP_NAME:-}" ]]; then
  parameters+=("appName=${APP_NAME}")
fi

az deployment group create \
  --name "$DEPLOYMENT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters "${parameters[@]}" \
  --output json >/dev/null

fqdn="$(az deployment group show --name "$DEPLOYMENT_NAME" --resource-group "$RESOURCE_GROUP" --query 'properties.outputs.fqdn.value' -o tsv)"
public_base_url="$(az deployment group show --name "$DEPLOYMENT_NAME" --resource-group "$RESOURCE_GROUP" --query 'properties.outputs.publicBaseUrl.value' -o tsv)"
admin_user="$(az deployment group show --name "$DEPLOYMENT_NAME" --resource-group "$RESOURCE_GROUP" --query 'properties.parameters.bootstrapUser.value' -o tsv)"

cat <<SUMMARY

Deployment complete.

FQDN:          ${fqdn}
URL:           ${public_base_url}
Admin UI:      ${public_base_url}/ui/
Admin user:    ${admin_user}
Admin password: the BOOTSTRAP_PASSWORD value you supplied

Note: the first request after idle can take 15-30s because the app scales to zero.
SUMMARY
