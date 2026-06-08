"""'???' — answer a question by searching the web first.

Same tool-calling agent as `?`, but the system prompt requires the model
to start with a `web_search` call and follow promising results into
`fetch_url`. Use it when you actually want fresh information rather
than the model's prior knowledge.

Sessions are kept separate from ``?`` (per-pane, per-command) so a
web-first thread doesn't leak into local-knowledge answers and vice
versa.
"""

from __future__ import annotations

import sys

from .ask import run_cli

SEARCH_SYSTEM = (
    "You answer the user's question by searching the web. Start every "
    "response by calling `web_search` with a focused query derived from "
    "the question. If a result clearly contains the answer, follow it by "
    "calling `fetch_url` on its URL to read the page in full — don't "
    "answer from snippets alone when a fetch would give you the real "
    "content. You also have `read_file` for files in $HOME or $PWD if "
    "useful. Write a concise markdown answer and cite the URLs you used "
    "as a short list at the end. If a tool refuses, reason from what you "
    "have."
)


def main() -> int:
    return run_cli(sys.argv[1:], cmd="search", system=SEARCH_SYSTEM, err_label="???")


if __name__ == "__main__":
    raise SystemExit(main())
