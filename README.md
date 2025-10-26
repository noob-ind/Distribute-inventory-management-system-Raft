# Distributed Inventory (gRPC) — Milestone 1

Beginner-friendly starter for the AOS assignment up to **Milestone 1**:
- gRPC service definitions
- Client–server communication
- Basic authentication & session tokens
- Mock "domain-specific" LLM server and integration
- Clean structure + scripts

## 1) Setup

```bash
cd dist-inventory-raft-m1
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
./scripts/gen_protos.sh
```

If `grpc_tools.protoc` is not found, confirm the venv is active and retry.

## 2) Run servers

Open two terminals (with venv active):

**Terminal A – LLM server**
```bash
python llm_server/main_llm_server.py
```

**Terminal B – App server**
```bash
python server/app_server.py
```

You should see:
- LLM server: `listening on 50052`
- App server: `listening on 50051`

## 3) Run the client demo

Open a third terminal (venv active):
```bash
python client/client.py --demo
```

This does:
1. Login as `alice/password`
2. Fetch inventory
3. Place order (ORDER) for 2x `SKU-APPLE`
4. Ask LLM if `SKU-MILK` needs reorder
5. Logout

## 4) Notes

- This is **Milestone 1** only (no Raft yet). The inventory & sessions are in-memory.
- For Milestone 2, replace in-memory state with Raft-replicated log and state machine.
- Protos are intentionally close to the assignment's `login/logout/get/post` shape.

## 5) Troubleshooting

- If import errors for generated code occur, re-run `./scripts/gen_protos.sh`.
- If ports busy, ensure nothing else is on 50051/50052.
- If `grpc` not found, ensure venv and requirements are installed.
