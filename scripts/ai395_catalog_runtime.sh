#!/bin/bash
set -euo pipefail
printf '%s\n' 'retired: no external catalog runtime or tunnel is configured' >&2
printf '%s\n' 'use the local qbr/review workflow, or create a new owner-approved contract' >&2
exit 2
