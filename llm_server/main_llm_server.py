import time
from concurrent import futures
import grpc

# ✅ Correct package-relative imports
from . import llm_pb2 , llm_pb2_grpc


class LLMService(llm_pb2_grpc.LLMServiceServicer):
    def GetLLMAnswer(self, request, context):
        """Mock 'customer-facing' response based on product availability."""
        q = (request.query or "").lower()

        # Default polite response
        answer = "Thank you for checking! Please specify the product name."

        # Extract stock number if present
        stock = None
        if "current stock=" in q:
            try:
                stock = int(q.split("current stock=")[1].split()[0])
            except Exception:
                pass

        # Respond like a customer-facing assistant
        if stock is not None:
            if stock == 0:
                answer = "Sorry, that item is currently out of stock."
            elif stock < 3:
                answer = "Only a few units left! You may want to order soon."
            elif stock < 8:
                answer = "Yes, it's available — limited stock remaining."
            else:
                answer = "Good news! The item is available and ready to order."

        elif "available" in q:
            answer = "Yes, most items are available right now!"

        return llm_pb2.AskResponse(request_id=request.request_id, answer=answer)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    llm_pb2_grpc.add_LLMServiceServicer_to_server(LLMService(), server)
    server.add_insecure_port("[::]:50052")
    print("LLM Server listening on 50052 (Customer Mode)")
    server.start()
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()
