#!/usr/bin/env bash
set -euo pipefail
python -m grpc_tools.protoc -I=protos --python_out=server --grpc_python_out=server protos/auth.proto protos/inventory.proto
python -m grpc_tools.protoc -I=protos --python_out=llm_server --grpc_python_out=llm_server protos/llm.proto
echo "Protobufs generated."
