def connect(host, port):
    return f"{host}:{port}"


def disconnect(conn):
    print(f"Disconnecting {conn}")
