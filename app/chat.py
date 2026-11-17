"""Talk to Lori from the terminal. Edit prompt.md to change how she works."""

from .bot import reply


def main() -> None:
    print("Lori legal prep. Type a message. Type quit to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"quit", "exit", "q"}:
            break
        result = reply(text)
        print(f"lori> {result['reply']}")
        if result["sources"]:
            print(f"sources> {', '.join(result['sources'])}")
        if result["facts"]:
            print(f"notes> {'; '.join(result['facts'])}")
        print(f"control> {result['control']}")
        if result["control"] in {"end", "review", "handoff"}:
            break


if __name__ == "__main__":
    main()
