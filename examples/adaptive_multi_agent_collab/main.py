from collaboration import select_collaborations


def main() -> None:
    print("Adaptive multi-agent collaboration example")

    for collaboration in select_collaborations():
        print(
            f"{collaboration.initiator} -> "
            f"{collaboration.target}: "
            f"{collaboration.scheme.value}"
        )


if __name__ == "__main__":
    main()
