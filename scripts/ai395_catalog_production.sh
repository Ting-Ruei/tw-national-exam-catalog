#!/bin/bash
set -euo pipefail
printf '%s\n' 'retired: no external catalog production runtime is configured' >&2
printf '%s\n' 'create a new owner-approved deployment contract before adding a production command' >&2
exit 2
