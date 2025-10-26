import grpc
import getpass
from server import auth_pb2, auth_pb2_grpc, inventory_pb2, inventory_pb2_grpc


def show_inventory(inv_stub, token):
    """Fetch and display latest inventory from the server."""
    resp = inv_stub.Get(inventory_pb2.GetRequest(token=token, type="ALL"))
    if resp.status != "OK":
        print(f"Error fetching inventory: {resp.message}")
        return []
    print("\n=== Current Inventory ===")
    for idx, item in enumerate(resp.items, start=1):
        print(f"{idx}. {item.name} ({item.sku}) - Stock: {item.stock}")
    return resp.items


session_actions = []
def interactive_client():
    # Connect to the App Server
    with grpc.insecure_channel("localhost:50051") as channel:
        auth = auth_pb2_grpc.AuthServiceStub(channel)
        inv = inventory_pb2_grpc.InventoryServiceStub(channel)

        # --- Login phase ---
        print("=== Login ===")
        username = input("Enter username (e.g., Ankit/manager1): ").strip()
        password = getpass.getpass("Enter password: ").strip()

        login_resp = auth.Login(auth_pb2.LoginRequest(username=username, password=password))
        if login_resp.status != "OK":
            print(f"Login failed: {login_resp.message}")
            return
        token = login_resp.token
        print(f"Welcome, {username}!\n")

        # --- Determine role from server message ---
        if "manager" in login_resp.message.lower():
            role = "manager"
        else:
            role = "customer"

        # Keep track of purchased items
        purchase_summary = {}

        # --- Main loop ---
        while True:
            print("\nOptions:")

            if role == "manager":
                print("1. Add stock")
                print("2. View inventory")
                print("3. Logout")
                choice = input("Choose an option (1–3): ").strip()

                if choice == "1":
                    sku_choice = input("Enter SKU (e.g., SKU-APPLE): ").strip().upper()
                    qty_str = input("Enter quantity to add: ").strip()
                    if not qty_str.isdigit() or int(qty_str) <= 0:
                        print("Invalid quantity.")
                        continue
                    qty = int(qty_str)

                    # Add stock
                    post_resp = inv.Post(
                        inventory_pb2.PostRequest(
                            token=token,
                            type="ADD_STOCK",
                            sku=sku_choice,
                            qty=qty
                        )
                    )
                    print(f"{post_resp.status}: {post_resp.message}")

                    # ✅ Auto-refresh inventory after adding stock
                    show_inventory(inv, token)
                    if post_resp.status == "OK":
                        session_actions.append(f"Added {qty} units to {sku_choice}")



                elif choice == "2":
                    # ✅ Show inventory only once (no duplicate)
                    show_inventory(inv, token)


                elif choice == "3":

                    logout_resp = auth.Logout(auth_pb2.LogoutRequest(token=token))

                    print(f"{logout_resp.status}: {logout_resp.message}")

                    # ✅ Print session summary

                    if session_actions:

                        print("\n=== Session Summary ===")

                        for i, act in enumerate(session_actions, start=1):
                            print(f"{i}. {act}")

                    else:

                        print("\nNo actions performed this session.")

                    break


                else:
                    print("Invalid option, try again.")

            else:  # Customer menu
                print("1. Buy an item")
                print("2. Ask LLM about item availability")
                print("3. Logout")
                choice = input("Choose an option (1–3): ").strip()

                if choice == "1":
                    sku_choice = input("Enter SKU (e.g., SKU-APPLE): ").strip().upper()
                    qty_str = input("Enter quantity to order: ").strip()
                    if not qty_str.isdigit() or int(qty_str) <= 0:
                        print("Invalid quantity.")
                        continue
                    qty = int(qty_str)

                    # Place order
                    post_resp = inv.Post(
                        inventory_pb2.PostRequest(
                            token=token,
                            type="ORDER",
                            sku=sku_choice,
                            qty=qty
                        )
                    )
                    print(f"{post_resp.status}: {post_resp.message}")

                    # ✅ Auto-refresh inventory after purchase :todo
                    show_inventory(inv, token)
                    if post_resp.status == "OK":
                        session_actions.append(f"Bought {qty} of {sku_choice}")


                elif choice == "2":
                    sku_choice = input("Enter SKU to check availability: ").strip().upper()
                    llm_resp = inv.Post(
                        inventory_pb2.PostRequest(
                            token=token,
                            type="ASK_LLM",
                            sku=sku_choice,
                            qty=0
                        )
                    )
                    print(f"LLM says: {llm_resp.message}")


                elif choice == "3":

                    logout_resp = auth.Logout(auth_pb2.LogoutRequest(token=token))

                    print(f"{logout_resp.status}: {logout_resp.message}")

                    # ✅ Print session summary

                    if session_actions:

                        print("\n=== Session Summary ===")

                        for i, act in enumerate(session_actions, start=1):
                            print(f"{i}. {act}")

                    else:

                        print("\nNo actions performed this session.")

                    break


                else:
                    print("Invalid option, try again.")


def main():
    print("Connecting to server...")
    interactive_client()


if __name__ == "__main__":
    main()
