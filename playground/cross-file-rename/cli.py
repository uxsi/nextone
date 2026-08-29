import api


def run():
    msg = api.hi("CLI user")
    print(msg)


if __name__ == "__main__":
    run()
