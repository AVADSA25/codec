"""CODEC Skill: openai_demo_runner"""
SKILL_NAME = "openai_demo_runner"
SKILL_DESCRIPTION = "Executes OpenAI API requests including standard completion, streaming output, and raw response header extraction."
SKILL_TRIGGERS = ["run openai demo", "test openai api", "execute openai examples", "run openai code"]

import os
import json

def run(task, app="", ctx=""):
    try:
        from openai import OpenAI
        client = OpenAI()
        
        # Standard request
        print("----- standard request -----")
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": "Say this is a test",
                },
            ],
        )
        standard_result = completion.choices[0].message.content
        
        # Streaming request
        print("----- streaming request -----")
        stream = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": "How do I output all files in a directory using Python?",
                },
            ],
            stream=True,
        )
        stream_result = ""
        for chunk in stream:
            if not chunk.choices:
                continue
            stream_result += chunk.choices[0].delta.content
        stream_result += "\n"

        # Raw response headers
        print("----- custom response headers test -----")
        response = client.chat.completions.with_raw_response.create(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": "Say this is a test",
                }
            ],
        )
        completion_raw = response.parse()
        request_id = response.request_id
        raw_content = completion_raw.choices[0].message.content
        
        return f"Standard: {standard_result}\nStream: {stream_result.strip()}\nRaw ID: {request_id}\nRaw Content: {raw_content}"
    except Exception as e:
        return f"Error executing OpenAI demo: {str(e)}"