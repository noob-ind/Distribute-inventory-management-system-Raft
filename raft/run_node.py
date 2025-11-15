import argparse
import grpc
import threading
import time
import random
import sys
import os

# Ensure root project directory is importable
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from concurrent import futures

from server import app_server
from server import inventory_pb2, inventory_pb2_grpc
from raft.raft_node import (
    RaftState,
    RaftRPC,
    ELECTION_TIMEOUT_MIN,
    ELECTION_TIMEOUT_MAX,
    HEARTBEAT_INTERVAL,
    apply_committed_entries,
)


# ================================================================
# Start GRPC server (RaftService + AppServerWithRaft)
# ================================================================
def start_grpc_server(state, app_servicer, raft_servicer, port):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=30))

    # Add RAFT RPC SERVICES
    inventory_pb2_grpc.add_RaftServiceServicer_to_server(raft_servicer, server)

    # Add APP SERVER (AUTH + INVENTORY)
    app_server.register_services(server, app_servicer)

    # Bind IPv4 explicitly (avoids IPv6/IPv4 mismatch on Windows)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()

    print(f"[Node {state.node_id}] Running on port {port} - Role: {state.state}")
    return server


# ================================================================
# Election Thread (uses last_heartbeat to avoid premature elections)
# ================================================================
def election_loop(state: RaftState):
    # initial random deadline based on last_heartbeat
    timeout = random.uniform(ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX)
    deadline = state.last_heartbeat + timeout

    while True:
        time.sleep(0.05)

        with state.lock:
            # leader has no election duties
            if state.state == "leader":
                # refresh stubs so leader can contact followers
                state.refresh_stubs()
                # push deadline forward to avoid participating in election loop
                deadline = state.last_heartbeat + random.uniform(ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX)
                continue

            # If a recent heartbeat was seen, postpone election
            now = time.time()
            if now < deadline:
                continue

            # Timeout passed -> start election
            state.state = "candidate"
            state.current_term += 1
            state.voted_for = state.node_id

            votes = 1
            last_index = len(state.log) - 1
            last_term = state.log[last_index][0] if last_index >= 0 else 0

            # Ensure stubs exist before sending RPCs
            state.refresh_stubs()

            for pid, stub in state.stubs.items():
                if stub is None:
                    continue
                try:
                    req = inventory_pb2.VoteRequest(
                        term=state.current_term,
                        candidateId=state.node_id,
                        lastLogIndex=last_index,
                        lastLogTerm=last_term,
                    )
                    rep = stub.RequestVote(req, timeout=1)
                    if rep.voteGranted:
                        votes += 1
                except Exception:
                    # treat RPC failures as no vote
                    pass

            # Win Election? (peers now includes self)
            if votes > (len(state.peers) // 2):
                state.state = "leader"
                state.leader_id = state.node_id
                # when leader, set last_heartbeat to now so followers don't race
                state.last_heartbeat = time.time()
                print(f"\n🔥 Node {state.node_id} became LEADER (Term {state.current_term})\n")
            else:
                state.state = "follower"

            # new deadline computed from last_heartbeat
            timeout = random.uniform(ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX)
            deadline = state.last_heartbeat + timeout


# ================================================================
# Heartbeat / Log Replication Thread (leader only)
# ================================================================
def heartbeat_loop(state: RaftState):
    while True:
        time.sleep(HEARTBEAT_INTERVAL)

        with state.lock:
            if state.state != "leader":
                continue

            last_index = len(state.log) - 1
            last_term = state.log[last_index][0] if last_index >= 0 else 0

            # refresh stubs before broadcasting
            state.refresh_stubs()

            for pid, stub in state.stubs.items():
                if stub is None:
                    continue
                try:
                    req = inventory_pb2.AppendRequest(
                        term=state.current_term,
                        leaderId=state.node_id,
                        prevLogIndex=last_index,
                        prevLogTerm=last_term,
                        commitIndex=state.commit_index,
                        entries=[],  # heartbeat
                    )
                    stub.AppendEntries(req, timeout=5)
                except Exception:
                    # ignore failed heartbeats; followers may be down
                    pass


# ================================================================
# Apply Committed Logs Thread (Followers & Leader)
# ================================================================
def apply_loop(state: RaftState, app_servicer):
    """
    Continuously apply committed log entries to the state machine.
    """
    while True:
        try:
            apply_committed_entries(state, app_servicer.inventory.apply_business_logic)
        except Exception as e:
            # do not crash the thread on apply errors
            print("[APPLY ERROR]", e)
        time.sleep(0.05)


# ================================================================
# MAIN
# ================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peers", nargs="*", default=[])
    args = parser.parse_args()

    # Build peer dictionary (from --peers argument)
    peers = {}
    for p in args.peers:
        pid, addr = p.split(":")[0], ":".join(p.split(":")[1:])
        peers[int(pid)] = addr

    # IMPORTANT: add this node's own address into peers so forwarding and replication work
    peers[args.id] = f"127.0.0.1:{args.port}"


    # Create Raft State (peers now includes self)
    state = RaftState(args.id, peers)

    # App server (wrapped with Raft)
    app_servicer = app_server.AppServerWithRaft(state)

    # Raft RPC handler
    raft_servicer = RaftRPC(state)

    # Start GRPC server
    grpc_server = start_grpc_server(state, app_servicer, raft_servicer, args.port)

    # Start background threads
    threading.Thread(target=election_loop, args=(state,), daemon=True).start()
    threading.Thread(target=heartbeat_loop, args=(state,), daemon=True).start()
    threading.Thread(target=apply_loop, args=(state, app_servicer), daemon=True).start()

    grpc_server.wait_for_termination()


if __name__ == "__main__":
    main()
