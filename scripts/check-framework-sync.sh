#!/bin/bash
# Check framework mirror synchronization.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/framework"
TARGET_DIR="$ROOT_DIR/template/.framework/framework"

echo "Checking framework sync status..."

DIFF_OUTPUT=$(diff -r \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".pytest_cache" \
    --exclude=".mypy_cache" \
    --exclude=".ruff_cache" \
    "$SOURCE_DIR" "$TARGET_DIR") || true

if [ -z "$DIFF_OUTPUT" ]; then
    echo "✅ framework/ and template/.framework/framework/ are in sync"
    exit 0
else
    echo "❌ ERROR: framework/ and template/.framework/framework/ are OUT OF SYNC"
    echo ""
    echo "$DIFF_OUTPUT"
    echo ""
    echo "Please run:"
    echo "  make sync-framework"
    echo ""
    echo "Or:"
    echo "  ./scripts/sync-framework-to-template.sh"
    exit 1
fi
