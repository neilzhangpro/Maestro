def countdown(n: int) -> list[str]:
    return [f"{i}..." for i in range(n, 0, -1)] + ["Go!"]


def main():
    for item in countdown(5):
        print(item)


if __name__ == "__main__":
    main()
