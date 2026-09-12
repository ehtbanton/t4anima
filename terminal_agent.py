#!/usr/bin/env python3
"""
StrokeGuard Terminal Agent - OpenAI Assistants API with Code Interpreter
Interactive CLI that can query patient data, run Python analysis, and generate insights.
"""

import os
import json
import time
from openai import OpenAI

client = OpenAI()

SYSTEM_PROMPT = """You are StrokeGuard AI, an NHS clinical decision support terminal agent.

You have access to a JSON file containing synthetic patient records with stroke risk factors.
Each patient has: id, age, sex, smoking status, blood pressure (sbp/dbp), conditions, and a timeline of clinical events (diagnoses, reports, GP visits, hospital attendances).

You can run Python code to:
- Query and filter patients by risk factors
- Calculate statistics (mean BP, condition prevalence, etc.)
- Identify high-risk patients who need intervention
- Analyze temporal patterns in clinical events
- Generate risk scores based on NICE guidelines

When analyzing data:
1. Load from 'patients_final.json'
2. Be specific with numbers and patient IDs
3. Prioritize actionable clinical insights
4. Reference UK NHS/NICE guidelines where relevant

You're a powerful clinical analytics terminal. Be concise but thorough."""


def create_assistant():
    """Create or retrieve the StrokeGuard assistant."""
    assistants = client.beta.assistants.list(limit=20)
    for a in assistants.data:
        if a.name == "StrokeGuard Terminal Agent":
            return a

    return client.beta.assistants.create(
        name="StrokeGuard Terminal Agent",
        instructions=SYSTEM_PROMPT,
        model="gpt-4o",
        tools=[{"type": "code_interpreter"}]
    )


def upload_patient_data():
    """Upload patient data for Code Interpreter."""
    with open("data/patients_final.json", "rb") as f:
        return client.files.create(file=f, purpose="assistants")


def run_agent():
    """Main agent loop."""
    print("\n" + "=" * 60)
    print("  STROKEGUARD TERMINAL AGENT")
    print("  OpenAI Code Interpreter + Patient Data")
    print("=" * 60)
    print("\nInitializing...")

    assistant = create_assistant()
    file = upload_patient_data()

    thread = client.beta.threads.create()

    print(f"Ready. Type queries about patient data. 'quit' to exit.\n")
    print("Examples:")
    print("  > How many patients have AFib?")
    print("  > Find the 5 highest risk patients by blood pressure")
    print("  > Plot the age distribution of diabetic patients")
    print("  > Which patients had a hospital admission after an abnormal ECG?")
    print()

    while True:
        try:
            user_input = input("\033[92m❯ \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ('quit', 'exit', 'q'):
            break

        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=user_input,
            attachments=[{"file_id": file.id, "tools": [{"type": "code_interpreter"}]}]
        )

        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant.id
        )

        print("\033[90mThinking...\033[0m", end="", flush=True)

        while run.status in ("queued", "in_progress"):
            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            print(".", end="", flush=True)

        print("\r" + " " * 40 + "\r", end="")

        if run.status == "completed":
            messages = client.beta.threads.messages.list(thread_id=thread.id, limit=1)
            for msg in messages.data:
                if msg.role == "assistant":
                    for block in msg.content:
                        if block.type == "text":
                            print(f"\n\033[94m{block.text.value}\033[0m\n")
                        elif block.type == "image_file":
                            print(f"\n[Generated image: {block.image_file.file_id}]\n")
        elif run.status == "failed":
            print(f"\n\033[91mError: {run.last_error}\033[0m\n")
        else:
            print(f"\n\033[93mRun ended with status: {run.status}\033[0m\n")

    print("\nCleaning up...")
    try:
        client.files.delete(file.id)
    except:
        pass
    print("Goodbye!")


if __name__ == "__main__":
    run_agent()
