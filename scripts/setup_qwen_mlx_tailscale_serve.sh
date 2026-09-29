#!/bin/bash
set -euo pipefail
printf '%s\n' 'retired: remote model serving is disabled' >&2
printf '%s\n' 'create a new owner-approved local task contract before configuring a provider' >&2
exit 2
