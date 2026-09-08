"""
Omnisight web service.

Serves a chat page anyone can open — no Google Cloud account, no sign-in — and
runs the Omnisight agent behind it. A director can paste or upload tomorrow's
call sheet and ask what to expect.
"""

import asyncio
import os
import traceback
import uuid

from flask import Flask, jsonify, request, send_from_directory
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent_def import root_agent

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "omnisight"

app = Flask(__name__, static_folder=None)
_runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)

# How a call sheet is handed to the agent. Kept here rather than in the system
# prompt so the deployed agent and this page stay in step.
CALL_SHEET_PREAMBLE = (
    "The director has attached a call sheet. Use it for locations, setups, "
    "camera assignments, operators and pack conditions. Cross-reference it "
    "against what the fleet actually recorded in comparable conditions by "
    "calling get_fleet_status. Ground your advice in those readings.\n\n"
    "CALL SHEET\n"
    "----------\n"
    "{sheet}\n"
    "----------\n\n"
    "DIRECTOR'S QUESTION: {question}"
)


async def _ask(session_id, text):
    """Run one turn through the agent and return its final text."""
    try:
        await _runner.session_service.create_session(
            app_name=APP_NAME, user_id=session_id, session_id=session_id
        )
    except Exception:
        pass  # session already exists; carry on

    message = types.Content(role="user", parts=[types.Part(text=text)])
    reply = []
    tools_used = []

    async for event in _runner.run_async(
        user_id=session_id, session_id=session_id, new_message=message
    ):
        for call in event.get_function_calls() or []:
            if call.name not in tools_used:
                tools_used.append(call.name)

        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    reply.append(part.text)

    return "".join(reply).strip(), tools_used


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    question = (body.get("message") or "").strip()
    sheet = (body.get("call_sheet") or "").strip()
    session_id = body.get("session_id") or f"web-{uuid.uuid4().hex[:12]}"

    if not question:
        return jsonify({"error": "Ask a question first."}), 400

    text = (
        CALL_SHEET_PREAMBLE.format(sheet=sheet, question=question)
        if sheet
        else question
    )

    try:
        reply, tools = asyncio.run(_ask(session_id, text))
    except Exception as exc:
        traceback.print_exc()
        return jsonify({
            "error": "The agent could not complete that. Try again.",
            "detail": f"{type(exc).__name__}: {exc}",
        }), 500

    return jsonify({
        "reply": reply or "No answer came back. Try rephrasing the question.",
        "tools": tools,
        "session_id": session_id,
    })


@app.route("/healthz")
def healthz():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
