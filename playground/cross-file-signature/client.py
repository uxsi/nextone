from utils import connect


def main():
    addr = connect("remote.host", 9090)
    print(f"Connected to {addr}")


if __name__ == "__main__":
    main()
