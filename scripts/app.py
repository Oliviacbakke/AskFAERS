"""AskFAERS web app.   Run from the project folder:

    source .venv/bin/activate
    export ANTHROPIC_API_KEY="your-key-here"
    streamlit run scripts/app.py

This file only draws the page. All querying goes through nl2sql.ask(), which runs every query through the
guardrails in sql_guard.py. The app never opens the database itself.
"""
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_helpers as h  # noqa: E402
import nl2sql  # noqa: E402

DB_PATH = Path(os.environ.get("FAERS_DB", Path(__file__).resolve().parent.parent / "data" / "faers.db"))

st.set_page_config(page_title="AskFAERS", layout="wide")


@st.cache_resource
def get_client():
    import anthropic
    return anthropic.Anthropic()


def set_question(text: str) -> None:
    st.session_state["question"] = text


def show_answer(ans) -> None:
    if ans.interpretation:
        st.caption(f"How I read your question: {ans.interpretation}")
    if ans.status == "refused":
        st.info(ans.message)
        return
    if ans.status == "failed":
        st.error(ans.message or "I couldn't answer that question. Try rephrasing it.")
        return

    columns = h.unique_columns(ans.columns)
    st.dataframe([dict(zip(columns, row)) for row in ans.rows])
    note = " More rows exist than are shown." if ans.truncated else ""
    st.caption(f"{len(ans.rows)} row(s).{note} {ans.coverage}")
    st.download_button("Download as CSV", h.rows_to_csv(ans.columns, ans.rows), "askfaers_result.csv", "text/csv")
    # The caveats are never hidden: they sit directly under every table.
    st.warning("**Read this with care**\n\n" + "\n".join(f"- {c}" for c in ans.caveats))
    with st.expander("Show the SQL query that was run"):
        st.code(ans.sql, language="sql")


# ---- page ------------------------------------------------------------------------------------------------------
st.title("AskFAERS")
st.write("Ask a plain-English question about FDA adverse event reports. You'll get a table, the SQL behind it, "
         "and reminders about what the numbers can and can't tell you.")
st.info(h.DISCLAIMER)

with st.sidebar:
    st.header("Settings")
    model_label = st.selectbox("Model", list(h.MODEL_CHOICES), index=0)
    st.header("About")
    st.write("Data: FDA Adverse Event Reporting System (FAERS), loaded locally. A report says an event was reported, "
             "not that a drug caused it. Counts are numbers of reports, not risk rates.")
    st.write("Safety: Claude writes the SQL, but every query is checked by a read-only guard that only allows the "
             "approved tables, with a time limit and a row cap.")

if not DB_PATH.exists():
    st.error(f"Database not found at {DB_PATH}. Load the FAERS data first (see the README).")
    st.stop()
if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error("ANTHROPIC_API_KEY is not set. Stop the app, run `export ANTHROPIC_API_KEY=\"your-key\"`, and start it again.")
    st.stop()

st.session_state.setdefault("asked", 0)
st.session_state.setdefault("history", [])

st.write("Try an example:")
for example in h.EXAMPLE_QUESTIONS:
    st.button(example, key=f"ex_{example}", on_click=set_question, args=(example,))

question = st.text_input("Your question", key="question", max_chars=nl2sql.MAX_QUESTION_CHARS)
left = h.questions_left(st.session_state["asked"])
go = st.button("Ask", type="primary", disabled=left == 0)
if left == 0:
    st.warning("You've reached the question limit for this session. Reload the page to start a new session.")

if go and question.strip():
    try:
        with st.spinner("Thinking..."):
            answer = nl2sql.ask(question, DB_PATH, get_client(), model=h.MODEL_CHOICES[model_label])
        st.session_state["asked"] += 1
        st.session_state["history"].insert(0, answer)
    except Exception as exc:  # never show raw errors, which could include request details
        st.error(h.friendly_api_error(exc))

history = st.session_state["history"]
if history:
    st.subheader(history[0].question)
    show_answer(history[0])
    if len(history) > 1:
        with st.expander(f"Earlier questions ({len(history) - 1})"):
            for old in history[1:]:
                st.markdown(f"**{old.question}**")
                show_answer(old)
                st.divider()
