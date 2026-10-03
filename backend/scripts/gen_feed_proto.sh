#!/usr/bin/env bash
# Regenerate app/upstox/feed/MarketDataFeedV3_pb2.py from the committed .proto.
# grpcio-tools is only needed for this step (an ephemeral env), not at runtime.
# The generated code embeds the protobuf version it was made with: the runtime pinned in
# pyproject.toml must be >= that. Check with: uv run python -c "import app.upstox.feed.MarketDataFeedV3_pb2"
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --no-project --with "grpcio-tools" python -m grpc_tools.protoc \
  -I app/upstox/feed --python_out=app/upstox/feed --pyi_out=app/upstox/feed \
  app/upstox/feed/MarketDataFeedV3.proto
echo "generated app/upstox/feed/MarketDataFeedV3_pb2.py(.pyi)"
