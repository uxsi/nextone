from utils import connect


def start_server():
    addr = connect("localhost", 8080)
    print(f"Server listening on {addr}")


if __name__ == "__main__":
    start_server()
