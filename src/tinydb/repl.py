"""Interactive SQL shell for tinydb; stdlib-only and isolated from the MVP core."""

PRIMARY_PROMPT_PREFIX = "tinydb"
CONTINUATION_PROMPT = "...> "


def _make_prompt(db_path: str) -> str:
    return f"{PRIMARY_PROMPT_PREFIX}> [{db_path}] "


def _read_one_statement(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None


def main() -> int:
    """Run the tinydb REPL."""
    return 0