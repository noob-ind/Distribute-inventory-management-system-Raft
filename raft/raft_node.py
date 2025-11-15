import threading
import time
import grpc
import traceback

from server import inventory_pb2, inventory_pb2_grpc

# ===============================
# Raft Timing Constants
# ===============================
ELECTION_TIMEOUT_MIN = 0.3
ELECTION_TIMEOUT_MAX = 0.6
HEARTBEAT_INTERVAL = 0.15


# ===============================
# Helper: normalize gRPC target (force IPv4)
# ===============================
def normalize_target(addr: str) -> str:
    """
    Convert 'localhost:51002' -> 'ipv4:127.0.0.1:51002'
    If the addr already has an ipv4:/ipv6: prefix, return as-is.
    """
    if not addr:
        return addr
    if addr.startswith("ipv4:") or addr.startswith("ipv6:"):
        return addr
    # replace localhost with explicit IPv4
    host_port = addr.split(":", 1)
    if host_port[0] == "localhost":
        addr = "127.0.0.1:" + host_port[1] if len(host_port) > 1 else "127.0.0.1"
    # prefix with ipv4:
    return f"ipv4:{addr}"


# ===============================
# Raft State Object
# ===============================
class RaftState:
    def __init__(self, node_id, peers):
        """
        node_id: int
        peers: dict of peer_id -> "host:port" (strings)
               include all peers (usually excluding self) — code tolerates self present
        """
        self.node_id = node_id
        self.peers = peers                    # dict {2: "localhost:51002", 3: "localhost:51003"}

        # Persistent Raft State
        self.current_term = 0
        self.voted_for = None
        self.log = []                         # list of (term, command_string)

        # Volatile State
        self.commit_index = -1
        self.last_applied = -1

        # Node Role: follower, candidate, leader
        self.state = "follower"
        self.leader_id = None

        # Track last heartbeat time (used to prevent premature elections)
        self.last_heartbeat = time.time()

        # Lock for Raft shared data
        self.lock = threading.Lock()

        # Create stubs for peer Raft RPCs (lazy/freshened by refresh_stubs)
        self.stubs = {}
        self.refresh_stubs()

    def refresh_stubs(self):
        """
        Recreate stubs for all peers. This uses normalize_target to force IPv4
        and recreates stubs if they are missing. We avoid creating a stub for
        self.node_id (no RPC to self).
        """
        for pid, addr in self.peers.items():
            if pid == self.node_id:
                # skip creating a stub for self
                self.stubs[pid] = None
                continue
            try:
                target = normalize_target(addr)
                # recreate channel + stub to avoid stale channels
                ch = grpc.insecure_channel(target)
                self.stubs[pid] = inventory_pb2_grpc.RaftServiceStub(ch)
            except Exception:
                # if creation fails, store None and continue
                self.stubs[pid] = None


# ===============================
# Raft RPC Handler
# ===============================
class RaftRPC(inventory_pb2_grpc.RaftServiceServicer):
    def __init__(self, state: RaftState):
        self.state = state

    # -------------------------------------
    # Handle RequestVote RPC
    # -------------------------------------
    def RequestVote(self, request, context):
        with self.state.lock:
            # debug
            print(f"[{time.strftime('%H:%M:%S')}] [Node {self.state.node_id}] RequestVote from {request.candidateId} term={request.term}")
            try:
                if request.term < self.state.current_term:
                    return inventory_pb2.VoteReply(term=self.state.current_term, voteGranted=False)

                if request.term > self.state.current_term:
                    self.state.current_term = request.term
                    self.state.voted_for = None
                    self.state.state = "follower"

                last_index = len(self.state.log) - 1
                last_term = self.state.log[last_index][0] if last_index >= 0 else 0

                up_to_date = (
                    request.lastLogTerm > last_term or
                    (request.lastLogTerm == last_term and request.lastLogIndex >= last_index)
                )

                if (self.state.voted_for is None or self.state.voted_for == request.candidateId) and up_to_date:
                    self.state.voted_for = request.candidateId
                    print(f"[{time.strftime('%H:%M:%S')}] [Node {self.state.node_id}] VOTED for {request.candidateId} term={self.state.current_term}")
                    return inventory_pb2.VoteReply(term=self.state.current_term, voteGranted=True)

                return inventory_pb2.VoteReply(term=self.state.current_term, voteGranted=False)
            except Exception as e:
                print("[RequestVote ERROR]", e)
                traceback.print_exc()
                return inventory_pb2.VoteReply(term=self.state.current_term, voteGranted=False)

    # -------------------------------------
    # Handle AppendEntries (Heartbeat + Log Replication)
    # -------------------------------------
    def AppendEntries(self, request, context):
        with self.state.lock:
            # debug
            print(f"[{time.strftime('%H:%M:%S')}] [Node {self.state.node_id}] AppendEntries from leader {request.leaderId} term={request.term} entries={len(request.entries)}")
            try:
                if request.term < self.state.current_term:
                    return inventory_pb2.AppendReply(term=self.state.current_term, success=False)

                # If leader's term is newer, update local term & revert to follower
                if request.term > self.state.current_term:
                    self.state.current_term = request.term
                    self.state.voted_for = None
                    self.state.state = "follower"

                # Accept leader id and update heartbeat
                self.state.leader_id = request.leaderId
                self.state.last_heartbeat = time.time()

                # Check prev log consistency
                prev = request.prevLogIndex
                if prev >= 0:
                    if prev >= len(self.state.log):
                        # follower log too short
                        return inventory_pb2.AppendReply(term=self.state.current_term, success=False)
                    if self.state.log[prev][0] != request.prevLogTerm:
                        # term mismatch
                        return inventory_pb2.AppendReply(term=self.state.current_term, success=False)

                # Append new entries (if any)
                if request.entries:
                    # truncate any conflicting entries and append
                    self.state.log = self.state.log[:prev + 1]
                    for e in request.entries:
                        self.state.log.append((request.term, e))

                # Advance commit index if leader's commitIndex is greater
                if request.commitIndex > self.state.commit_index:
                    self.state.commit_index = min(request.commitIndex, len(self.state.log) - 1)

                return inventory_pb2.AppendReply(term=self.state.current_term, success=True)

            except Exception as e:
                print("[AppendEntries ERROR]", e)
                traceback.print_exc()
                return inventory_pb2.AppendReply(term=self.state.current_term, success=False)


# ===============================
# APPLY committed log entries
# ===============================
def apply_committed_entries(state: RaftState, apply_fn):
    """
    Called from run_node.py apply_loop thread.
    apply_fn is InventoryService.apply_business_logic() and expects (ctype, sku, qty, context).
    We call with context=None since the apply loop is internal.
    """
    try:
        with state.lock:
            while state.last_applied < state.commit_index:
                state.last_applied += 1
                term, cmd = state.log[state.last_applied]

                # parse command and call apply_fn
                parts = cmd.split("|")
                ctype = parts[0].upper() if len(parts) > 0 else ""
                sku = parts[1].upper() if len(parts) > 1 else ""
                qty = int(parts[2]) if len(parts) > 2 and parts[2] != "" else 0

                try:
                    # apply with context=None (internal)
                    apply_fn(ctype, sku, qty, None)
                except TypeError:
                    # some versions may expect apply_fn(ctype, sku, qty) — handle both
                    try:
                        apply_fn(ctype, sku, qty)
                    except Exception as e:
                        print("[APPLY ERROR] apply_fn call failed:", e)
                        traceback.print_exc()
                except Exception as e:
                    print("[APPLY ERROR] during apply_fn:", e)
                    traceback.print_exc()
    except Exception as e_outer:
        print("[apply_committed_entries ERROR]", e_outer)
        traceback.print_exc()
