import os
import time
import uuid
import traceback
import sys
from concurrent import futures
import grpc

# Import protobuf services
from server import auth_pb2, auth_pb2_grpc, inventory_pb2, inventory_pb2_grpc
from llm_server import llm_pb2, llm_pb2_grpc

# -------------------------------------------------------------------------
# GLOBAL DATA (Shared logically; independent physical copies per node)
# -------------------------------------------------------------------------
USERS = {
    "Alankrit": {"password": "Alankrit", "role": "customer"},
    "Ankit": {"password": "Diti", "role": "customer"},
    "Ajay": {"password": "Ajay", "role": "manager"},
}

SESSIONS = {}
INVENTORY = {
    "SKU-MOBILE PHONE": {"name": "Mobile Phone", "stock": 10},
    "SKU-POWERBANK": {"name": "Powerbank", "stock": 5},
    "SKU-LAPTOP": {"name": "Laptop", "stock": 8},
}

# -------------------------------------------------------------------------
# AUTH HELPERS
# -------------------------------------------------------------------------
def require_auth(token: str):
    if not token or token not in SESSIONS:
        return False, None, None
    s = SESSIONS[token]
    return True, s["username"], s["role"]

# -------------------------------------------------------------------------
# AUTH SERVICE
# -------------------------------------------------------------------------
class AuthService(auth_pb2_grpc.AuthServiceServicer):
    def Login(self, request, context):
        username = request.username.strip()
        password = request.password

        user = USERS.get(username)
        if user and user["password"] == password:
            token = str(uuid.uuid4())
            SESSIONS[token] = {"username": username, "role": user["role"]}
            return auth_pb2.LoginResponse(status="OK", token=token,
                                          message=f"Welcome {username}!")
        return auth_pb2.LoginResponse(status="ERROR", token="", message="Invalid credentials")

    def Logout(self, request, context):
        if request.token in SESSIONS:
            del SESSIONS[request.token]
            return auth_pb2.StatusReply(status="OK", message="Logged out.")
        return auth_pb2.StatusReply(status="ERROR", message="Invalid token")

# -------------------------------------------------------------------------
# INVENTORY SERVICE (Business Logic)
# -------------------------------------------------------------------------
class InventoryService(inventory_pb2_grpc.InventoryServiceServicer):

    def __init__(self, llm_channel_target="127.0.0.1:50052"):
        self.llm_channel_target = llm_channel_target

    def apply_business_logic(self, ctype, sku, qty, context=None):
        sku = sku.upper()

        try:
            if sku not in INVENTORY:
                return auth_pb2.StatusReply(status="ERROR",
                                            message=f"Unknown SKU {sku}")

            if ctype == "ORDER":
                if INVENTORY[sku]["stock"] < qty:
                    return auth_pb2.StatusReply(status="ERROR",
                                                message="Insufficient stock")
                INVENTORY[sku]["stock"] -= qty
                return auth_pb2.StatusReply(status="OK",
                    message=f"Order placed for {qty} of {INVENTORY[sku]['name']}")

            if ctype == "ADD_STOCK":
                INVENTORY[sku]["stock"] += qty
                return auth_pb2.StatusReply(status="OK",
                    message=f"Added {qty} units to {INVENTORY[sku]['name']}")

            if ctype == "ASK_LLM":
                try:
                    ch_target = f"ipv4:{self.llm_channel_target}"
                    with grpc.insecure_channel(ch_target) as ch:
                        stub = llm_pb2_grpc.LLMServiceStub(ch)
                        req_id = str(uuid.uuid4())
                        resp = stub.GetLLMAnswer(
                            llm_pb2.AskRequest(
                                request_id=req_id,
                                query=f"Should we reorder {INVENTORY[sku]['name']}? Current stock={INVENTORY[sku]['stock']}",
                                context="inventory"
                            ),
                            timeout=7
                        )
                        return auth_pb2.StatusReply(status="OK", message=resp.answer)
                except Exception as e:
                    return auth_pb2.StatusReply(status="ERROR",
                                                message=f"LLM error: {e}")

            return auth_pb2.StatusReply(status="ERROR", message="Unknown type")

        except Exception as e:
            traceback.print_exc()
            return auth_pb2.StatusReply(status="ERROR",
                                        message=f"Internal error: {e}")

    def Get(self, request, context):
        ok, user, role = require_auth(request.token)
        if not ok:
            return inventory_pb2.GetResponse(status="ERROR", items=[],
                                             message="Unauthorized")

        typ = (request.type or "ALL").upper()

        if typ == "ALL":
            items = [
                inventory_pb2.Item(sku=sku, name=d["name"], stock=d["stock"])
                for sku, d in INVENTORY.items()
            ]
            return inventory_pb2.GetResponse(status="OK", items=items,
                                             message=f"{len(items)} items")

        if typ == "ONE":
            sku = request.sku.upper()
            if sku not in INVENTORY:
                return inventory_pb2.GetResponse(status="ERROR",
                                                 items=[],
                                                 message=f"Unknown SKU {sku}")
            d = INVENTORY[sku]
            return inventory_pb2.GetResponse(status="OK",
                    items=[inventory_pb2.Item(sku=sku, name=d["name"], stock=d["stock"])],
                    message="1 item")

        return inventory_pb2.GetResponse(status="ERROR", items=[],
                                         message="Unknown type")

# -------------------------------------------------------------------------
# RAFT-AWARE SERVER
# -------------------------------------------------------------------------
class AppServerWithRaft(inventory_pb2_grpc.InventoryServiceServicer):
    def __init__(self, raft_state):
        self.raft = raft_state
        self.inventory = InventoryService()
        self.auth = AuthService()

    # ---------------------------------------------------------------------
    # POST (Raft-flow)
    # ---------------------------------------------------------------------
    def Post(self, request, context):

        print(f"[{time.strftime('%H:%M:%S')}] [Node {self.raft.node_id}] POST "
              f"{request.type} {request.sku} {request.qty} | role={self.raft.state}")
        sys.stdout.flush()

        # AUTHENTICATION
        if request.token not in SESSIONS:
            return auth_pb2.StatusReply(status="ERROR", message="Unauthorized")

        # ---------------------------------------------------------
        # FOLLOWER: forward request to leader
        # ---------------------------------------------------------
        if self.raft.state != "leader":
            leader_id = self.raft.leader_id

            if leader_id is None:
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                return auth_pb2.StatusReply(status="ERROR", message="Leader unknown")

            leader_addr = self.raft.peers.get(leader_id)
            if not leader_addr:
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                return auth_pb2.StatusReply(status="ERROR",
                                            message="Leader address missing")

            ch_target = f"ipv4:{leader_addr}"

            print(f"[Node {self.raft.node_id}] FORWARD → {ch_target}")
            sys.stdout.flush()

            try:
                with grpc.insecure_channel(ch_target) as ch:
                    stub = inventory_pb2_grpc.InventoryServiceStub(ch)
                    resp = stub.Post(request, timeout=12)
                    return resp
            except Exception as e:
                print(f"[Node {self.raft.node_id}] FORWARD FAILED: {e}")
                traceback.print_exc()
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                return auth_pb2.StatusReply(status="ERROR",
                                            message=f"Forward failed: {e}")

        # ---------------------------------------------------------
        # LEADER LOGIC
        # ---------------------------------------------------------
        cmd = f"{request.type}|{request.sku}|{request.qty}"

        with self.raft.lock:
            self.raft.log.append((self.raft.current_term, cmd))
            index = len(self.raft.log) - 1

        print(f"[Leader {self.raft.node_id}] LOG += {cmd} (idx={index})")
        sys.stdout.flush()

        # ---------------------------------------------------------
        # REPLICATION
        # ---------------------------------------------------------
        self.raft.refresh_stubs()

        success = 1
        total = len(self.raft.peers)

        for pid, stub in self.raft.stubs.items():
            if pid == self.raft.node_id:
                continue

            try:
                prev = index - 1
                prev_term = self.raft.log[prev][0] if prev >= 0 else 0

                req = inventory_pb2.AppendRequest(
                    term=self.raft.current_term,
                    leaderId=self.raft.node_id,
                    prevLogIndex=prev,
                    prevLogTerm=prev_term,
                    commitIndex=self.raft.commit_index,
                    entries=[cmd]
                )

                print(f"[Leader {self.raft.node_id}] → AppendEntries → Node {pid}")
                sys.stdout.flush()

                rep = stub.AppendEntries(req, timeout=7)

                # Follower has higher term → we step down
                if rep.term > self.raft.current_term:
                    print(f"[Leader {self.raft.node_id}] TERM {rep.term} > my {self.raft.current_term} → STEPPING DOWN")
                    sys.stdout.flush()

                    with self.raft.lock:
                        self.raft.current_term = rep.term
                        self.raft.state = "follower"
                        self.raft.leader_id = None

                    return auth_pb2.StatusReply(status="ERROR",
                                                message="Lost leadership; retry")

                if rep.success:
                    success += 1

            except Exception as e:
                print(f"[Leader {self.raft.node_id}] AppendEntries FAILED to {pid}: {e}")
                traceback.print_exc()
                sys.stdout.flush()

        # ---------------------------------------------------------
        # MAJORITY COMMIT?
        # ---------------------------------------------------------
        if success > (total // 2):
            with self.raft.lock:
                self.raft.commit_index = index

            print(f"[Leader {self.raft.node_id}] COMMIT index={index} ({success}/{total})")
            sys.stdout.flush()

            ctype = request.type.upper()
            sku = request.sku.upper()
            qty = int(request.qty)

            result = self.inventory.apply_business_logic(ctype, sku, qty)
            return result

        print(f"[Leader {self.raft.node_id}] REPLICATION FAILED ({success}/{total})")
        sys.stdout.flush()
        return auth_pb2.StatusReply(status="ERROR", message="Replication failed")

    # ---------------------------------------------------------------------
    def Get(self, request, context):
        return self.inventory.Get(request, context)

# -------------------------------------------------------------------------
# REGISTER SERVICES
# -------------------------------------------------------------------------
def register_services(server, app_servicer):
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(app_servicer, server)
    auth_pb2_grpc.add_AuthServiceServicer_to_server(app_servicer.auth, server)
