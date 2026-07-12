"""scripts/parse_regulatory.py — re-derive data/corpus/*.md from data/raw/*.pdf with LlamaParse.

YOU DO NOT NEED TO RUN THIS. The parsed markdown is COMMITTED (data/corpus/), and
scripts/ingest_regulatory.py reads it. This script exists so the committed artifact is reproducible
rather than magical — and so nobody has to take the tier decision below on faith.

======================= RUN IT IN AN ISOLATED VENV. NOT THE APP'S. =======================
`llama-cloud-services` pulls 67 packages (llama-index-core, nltk, tiktoken, banks, ...) and it
UPGRADES SQLAlchemy 2.0.36 -> 2.0.51 and numpy 2.2.1 -> 2.5.1. Installing it into the app's venv
would silently move the DATABASE layer underneath a project whose whole thesis is the database —
the Item-8 deepeval dependency break, landing somewhere much worse. It is therefore NOT in
requirements.txt and never should be.

    python -m venv /tmp/parsevenv
    /tmp/parsevenv/bin/pip install llama-cloud-services
    LLAMA_CLOUD_API_KEY=... /tmp/parsevenv/bin/python scripts/parse_regulatory.py

============================ THE TIER DECISION, MEASURED ============================
`parse_mode="parse_page_with_llm"` is the Cost-effective tier (3 credits/page; 58 pages = 174
credits). Fast (1 credit) is unusable: it emits spatial text with NO markdown, so there are no
headings, no section paths, and structure-aware chunking becomes impossible.

Agentic (10 credits/page) WAS measured on FFIEC rather than assumed, and it is WORSE:

    payload      129 bullets = 129 bullets. IDENTICAL. Neither tier loses a single red flag.
    structure    Agentic emits real ##/### levels — but SIX of FFIEC's 29 section headings are not
                 headings in its output. Four are demoted to **bold body text** and two vanish as
                 strings entirely, their red flags silently absorbed into the preceding section. Its
                 hierarchy is also internally inconsistent (Insurance and Shell Company Activity
                 become H2 SIBLINGS of the Money-Laundering part they belong under), and it drops the
                 Money-Laundering "Funds Transfers" section while keeping the Terrorist-Financing one
                 — which would resolve the two-sections-with-one-name collision the WRONG way.

Cost-effective is FLAT (every heading is H1, in all five documents) but COMPLETE. Agentic is
hierarchical but LOSSY. For a deterministic spine rule, complete-and-flat is strictly better raw
material — and 3.3x cheaper. The spine is recovered in app/services/regulation.py instead.
"""

import os
import sys
from pathlib import Path

from llama_cloud_services import LlamaParse  # NOT an app dependency — see the docstring.

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "corpus"

DOCS = [
    "Appendix-F-Money-Laundering-and-Terrorist-Financing.pdf",   # FFIEC BSA/AML Manual, Appendix F
    "Virtual-Assets-Red-Flag-Indicators.pdf",                    # FATF (2020)
    "Trade-Based-Money-Laundering-Risk-Indicators.pdf",          # FATF / Egmont Group (2021)
    "FIN-2014-A005.pdf",                                         # FinCEN advisory
    "fin-2010-a001.pdf",                                         # FinCEN advisory
]


def main() -> None:
    key = os.environ.get("LLAMA_CLOUD_API_KEY")
    if not key:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("LLAMA_CLOUD_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        sys.exit("LLAMA_CLOUD_API_KEY is not set (env or .env)")

    OUT.mkdir(parents=True, exist_ok=True)
    parser = LlamaParse(
        api_key=key,
        parse_mode="parse_page_with_llm",   # Cost-effective. NOT Fast (no markdown), NOT Agentic.
        result_type="markdown",
    )

    for name in DOCS:
        src = RAW / name
        if not src.exists():
            sys.exit(f"missing source PDF: {src} (data/raw/ is gitignored — supply the PDFs)")
        dest = OUT / (src.stem + ".md")
        print(f"[parse] {name} -> {dest.name}", flush=True)
        result = parser.parse(str(src))
        md = "\n\n".join(d.text for d in result.get_markdown_documents(split_by_page=False))
        dest.write_text(md, encoding="utf-8")
        heads = sum(1 for ln in md.splitlines() if ln.lstrip().startswith("#"))
        print(f"          {len(md)} chars, {heads} headings", flush=True)

    print("\n[done] Re-run the gates — a re-parse that drifts MUST fail them, loudly:", flush=True)
    print("  pytest tests/test_regulatory_corpus.py     (the pinned per-document census)", flush=True)
    print("  scripts/verify_regulatory.py               (every red flag traced back to its PDF)",
          flush=True)


if __name__ == "__main__":
    main()
