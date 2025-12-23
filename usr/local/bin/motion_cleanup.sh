#!/bin/bash

# ===== CONFIG =====
TARGET_DIR="/var/lib/motion"
MAX_GB=20
DRY_RUN=false   # <-- IMPORTANT: true = no deletion
# ==================

MAX_BYTES=$((MAX_GB * 1024 * 1024 * 1024))

current_bytes=$(du -sb "$TARGET_DIR" | awk '{print $1}')

echo "Current usage: $((current_bytes / 1024 / 1024)) MB"

if [ "$current_bytes" -le "$MAX_BYTES" ]; then
    echo "Storage under limit. Nothing to do."
    exit 0
fi

echo "Over ${MAX_GB}GB limit. Selecting files for cleanup..."

mapfile -t files < <(find "$TARGET_DIR" -type f -name "*.mp4" -printf '%T@ %p\n' | sort -n | awk '{print $2}')

for file in "${files[@]}"; do
    filesize=$(stat -c%s "$file")

    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] Would delete: $file"
    else
        echo "Deleting: $file"
        rm -f "$file"
    fi

    current_bytes=$((current_bytes - filesize))

    if [ "$current_bytes" -le "$MAX_BYTES" ]; then
        break
    fi
done

echo "Cleanup complete."
