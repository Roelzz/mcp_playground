#!/usr/bin/env bash
set -euo pipefail

cat <<'WARNING'
⚠️  DESTRUCTIVE ACTION

Deleting the resource group destroys the Container Apps *environment*. Rebuilding produces a **different** `<env-id>` and therefore a **different FQDN**. Every URL already handed out to attendees will break permanently and cannot be recovered.
WARNING

yes_flag=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)
      yes_flag=1
      ;;
    --help|-h)
      echo "Usage: ./teardown.sh [--yes]"
      echo "Environment override: RESOURCE_GROUP (default: rg-agent-playground)"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
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

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-agent-playground}"

if [[ "$yes_flag" != "1" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Refusing to delete without typed confirmation on non-interactive stdin. Re-run with --yes for scripted teardown." >&2
    exit 1
  fi
  echo
  read -r -p "Type the resource group name '${RESOURCE_GROUP}' to permanently delete it: " confirmation
  if [[ "$confirmation" != "$RESOURCE_GROUP" ]]; then
    echo "Resource group name did not match. Teardown cancelled."
    exit 0
  fi
fi

if [[ "$(az group exists --name "$RESOURCE_GROUP")" != "true" ]]; then
  echo "Resource group '${RESOURCE_GROUP}' does not exist. Nothing to delete."
  exit 0
fi

az group delete --name "$RESOURCE_GROUP" --yes

echo "Deleted resource group '${RESOURCE_GROUP}'."
