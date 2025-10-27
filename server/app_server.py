import os
import time
import uuid
from concurrent import futures
import grpc

#:todo important for import
from server import auth_pb2, auth_pb2_grpc, inventory_pb2, inventory_pb2_grpc
from llm_server import llm_pb2, llm_pb2_grpc


#:todo we will add multiple clients as a part of milestone2
USERS = {
    "ankit": {"password": "admin", "role": "customer"},
    "alice": {"password": "password", "role": "customer"},
    "manager1": {"password": "admin", "role": "manager"},
}

SESSIONS = {}
INVENTORY = {
    "SKU-APPLE": {"name": "Apple", "stock": 10},
    "SKU-MILK": {"name": "Milk", "stock": 5},
    "SKU-BREAD": {"name": "Bread", "stock": 8},
}


def require_auth(token: str):
    if not token or token not in SESSIONS:
        return False, None, None

    sess = SESSIONS[token]


    if isinstance(sess, dict):
        return True, sess.get("username"), sess.get("role")
    else:

        return True, sess, "customer"




class AuthService(auth_pb2_grpc.AuthServiceServicer):


    def Login(self, request, context):
        username = request.username.strip()
        password = request.password
        user = USERS.get(username)
        if user and user["password"] == password:
            token = str(uuid.uuid4())
            SESSIONS[token] = {"username": username, "role": user["role"]}
            return auth_pb2.LoginResponse(
                status="OK",
                token=token,
                message=f"Welcome {username}! Logged in as {user['role']}."
            )
        return auth_pb2.LoginResponse(status="ERROR", token="", message="Invalid username or password")

    def Logout(self, request, context):
        token = request.token
        if token in SESSIONS:
            del SESSIONS[token]
            return auth_pb2.StatusReply(status="OK", message="Logged out.")
        return auth_pb2.StatusReply(status="ERROR", message="Invalid token.")


class InventoryService(inventory_pb2_grpc.InventoryServiceServicer):

    def __init__(self, llm_channel_target="localhost:50052"):
        self.llm_channel_target = llm_channel_target

    def Post(self, request, context):

        ok, user, role = require_auth(request.token)
        if not ok:
            return auth_pb2.StatusReply(status="ERROR", message="Unauthorized")

        sku = request.sku.strip().upper()
        qty = int(request.qty)
        typ = request.type.upper().strip()

        if sku not in INVENTORY:
            return auth_pb2.StatusReply(status="ERROR", message=f"Unknown SKU {sku}")


        if typ == "ORDER":
            if INVENTORY[sku]["stock"] < qty:
                return auth_pb2.StatusReply(
                    status="ERROR", message="Insufficient stock"
                )
            INVENTORY[sku]["stock"] -= qty
            return auth_pb2.StatusReply(
                status="OK",
                message=f"Order placed by {user} for {qty} of {INVENTORY[sku]['name']}",
            )


        elif typ == "ADD_STOCK":
            if role != "manager":
                return auth_pb2.StatusReply(
                    status="ERROR",
                    message="Permission denied: Only inventory managers can add stock.",
                )
            INVENTORY[sku]["stock"] += qty
            return auth_pb2.StatusReply(
                status="OK",
                message=f"Manager {user} added {qty} units to {INVENTORY[sku]['name']}",
            )


        elif typ == "ASK_LLM":
            with grpc.insecure_channel(self.llm_channel_target) as ch:
                stub = llm_pb2_grpc.LLMServiceStub(ch)
                req_id = str(uuid.uuid4())
                resp = stub.GetLLMAnswer(
                    llm_pb2.AskRequest(
                        request_id=req_id,
                        query=f"Should we reorder {INVENTORY[sku]['name']}? Current stock={INVENTORY[sku]['stock']}",
                        context="inventory",
                    )
                )
                return auth_pb2.StatusReply(status="OK", message=resp.answer)

        else:
            return auth_pb2.StatusReply(
                status="ERROR",
                message="Unknown type; use ORDER, ADD_STOCK, or ASK_LLM",
            )

    def Get(self, request, context):
        ok, user, role = require_auth(request.token)

        if not ok:
            return inventory_pb2.GetResponse(
                status="ERROR", items=[], message="Unauthorized"
            )

        typ = (request.type or "ALL").upper().strip()
        if typ == "ALL":
            items = [
                inventory_pb2.Item(
                    sku=sku, name=data["name"], stock=data["stock"]
                )
                for sku, data in INVENTORY.items()
            ]
            return inventory_pb2.GetResponse(
                status="OK", items=items, message=f"{len(items)} items"
            )

        elif typ == "ONE":
            sku = request.sku.strip().upper()
            if sku not in INVENTORY:
                return inventory_pb2.GetResponse(
                    status="ERROR", items=[], message=f"Unknown SKU {sku}"
                )
            data = INVENTORY[sku]
            item = inventory_pb2.Item(
                sku=sku, name=data["name"], stock=data["stock"]
            )
            return inventory_pb2.GetResponse(
                status="OK", items=[item], message="1 item"
            )

        else:
            return inventory_pb2.GetResponse(
                status="ERROR", items=[], message="Unknown type; use ALL or ONE"
            )


def serve():

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    auth_pb2_grpc.add_AuthServiceServicer_to_server(AuthService(), server)
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(InventoryService(), server)
    server.add_insecure_port("[::]:50051")
    print("App Server listening on 50051")
    server.start()
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        print("\nStopping App Server...")
        server.stop(0)


if __name__ == "__main__":
    serve()
