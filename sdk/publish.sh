#!/bin/bash
# publish.sh — Build and publish synapserun to TestPyPI
#
# Prerequisites:
#   pip install build twine
#   Set TWINE_USERNAME and TWINE_PASSWORD for TestPyPI
#
# Usage:
#   ./sdk/publish.sh          # Build only
#   ./sdk/publish.sh --push   # Build and push to TestPyPI
set -euo pipefail

cd "$(dirname "$0")"

echo "=== Building synapserun ==="

# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build
python3 -m build

echo
echo "Built:"
ls -la dist/

if [ "${1:-}" = "--push" ]; then
    echo
    echo "=== Publishing to TestPyPI ==="
    python3 -m twine upload --repository testpypi dist/*
    echo
    echo "Install with:"
    echo "  pip install --index-url https://test.pypi.org/simple/ synapserun"
else
    echo
    echo "Dry run complete. To publish:"
    echo "  ./sdk/publish.sh --push"
fi
