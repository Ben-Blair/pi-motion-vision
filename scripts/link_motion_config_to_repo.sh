#!/bin/bash
#
# Backward-compatible entry point: links Motion config *and* hooks, bin, scripts.
# See scripts/link_repo_to_live.sh for details.

set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/link_repo_to_live.sh"
