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


def interactive_client():
    # Connect to the App Server
    with grpc.insecure_channel("localhost:50051") as channel:
        auth = auth_pb2_grpc.AuthServiceStub(channel)
        inv = inventory_pb2_grpc.InventoryServiceStub(channel)

        # --- Login phase ---
        print("=== Login ===")
        username = input("Enter username (e.g., Ankit): ").strip()
        password = getpass.getpass("Enter password: ").strip()

        login_resp = auth.Login(auth_pb2.LoginRequest(username=username, password=password))
        if login_resp.status != "OK":
            print(f"Login failed: {login_resp.message}")
            return
        token = login_resp.token
        print(f"Welcome, {username}!\n")

        # Keep track of purchased items
        purchase_summary = {}

        # --- Main loop ---
        while True:
            items = show_inventory(inv, token)

            print("\nOptions:")
            print("1. Buy an item")
            print("2. Ask LLM for restock suggestion")
            print("3. Logout")

            choice = input("Choose an option (1–3): ").strip()

            if choice == "1":
                sku_choice = input("Enter SKU (e.g., SKU-APPLE): ").strip().upper()
                qty_str = input("Enter quantity to order: ").strip()
                if not qty_str.isdigit() or int(qty_str) <= 0:
                    print("Invalid quantity.")
                    continue
                qty = int(qty_str)

                post_resp = inv.Post(
                    inventory_pb2.PostRequest(token=token, type="ORDER", sku=sku_choice, qty=qty)
                )
                print(f"{post_resp.status}: {post_resp.message}")

                # Record successful purchase
                if post_resp.status == "OK":
                    purchase_summary[sku_choice] = purchase_summary.get(sku_choice, 0) + qty

                # Refresh dynamic stock
                show_inventory(inv, token)

            elif choice == "2":
                sku_choice = input("Enter SKU to ask LLM about: ").strip().upper()
                llm_resp = inv.Post(
                    inventory_pb2.PostRequest(token=token, type="ASK_LLM", sku=sku_choice, qty=0)
                )
                print(f"LLM says: {llm_resp.message}")

            elif choice == "3":
                logout_resp = auth.Logout(auth_pb2.LogoutRequest(token=token))
                print(f"{logout_resp.status}: {logout_resp.message}")

                # Show purchase summary before exit
                if purchase_summary:
                    print("\n=== Purchase Summary ===")
                    for sku, qty in purchase_summary.items():
                        print(f"{sku}: {qty} item(s) purchased")
                else:
                    print("\nNo items purchased this session.")
                break

            else:
                print("Invalid option, try again.")


def main():
    print("Connecting to server...")
    interactive_client()


if __name__ == "__main__":
    main()
