import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Dict

# Add local pipecat to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipecat", "src"))

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from loguru import logger

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v2 import LocalSmartTurnAnalyzerV2
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai.llm import OpenAILLMService

from pipecat.services.whisper.stt import WhisperSTTServiceMLX, MLXModel
from pipecat.transports.base_transport import TransportParams
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor
from pipecat.transports.network.small_webrtc import SmallWebRTCTransport
from pipecat.transports.network.webrtc_connection import IceServer, SmallWebRTCConnection
from pipecat.processors.aggregators.llm_response import LLMUserAggregatorParams

from tts_mlx_isolated import TTSMLXIsolated

load_dotenv(override=True)

app = FastAPI()

pcs_map: Dict[str, SmallWebRTCConnection] = {}

ice_servers = [
    IceServer(
        urls="stun:stun.l.google.com:19302",
    )
]


def _load_pipecat_config():
    """Load user_name and assistant_name from config."""
    try:
        import json as _j
        with open(os.path.expanduser("~/.codec/config.json")) as _f:
            _c = _j.load(_f)
        return _c.get("user_name", ""), _c.get("assistant_name", "CODEC")
    except Exception:
        return "", "CODEC"

_PC_USER_NAME, _PC_ASSISTANT_NAME = _load_pipecat_config()
_PC_USER_REF = _PC_USER_NAME if _PC_USER_NAME else "the user"
_PC_GREET_NAME = f" {_PC_USER_NAME}" if _PC_USER_NAME else ""

SYSTEM_INSTRUCTION = f"""You are {_PC_ASSISTANT_NAME}, a JARVIS-class AI assistant running locally on a Mac Studio M1 Ultra. {f'The user is {_PC_USER_NAME}.' if _PC_USER_NAME else ''} You are {_PC_USER_REF}'s personal AI.

IMPORTANT: All conversations are saved to shared memory. If {_PC_USER_REF} asks you to remember something, a code, a task, or any information — confirm it is stored. A mix of JARVIS meets TARS-class AI. Running locally on a Mac Studio M1 Ultra with 64GB unified RAM. No cloud, no API overlords, pure local sovereignty via MLX. Your model is Qwen 3.5 35B, 4-bit quantized. You are fast, private, and entirely self-hosted.

Your input is text transcribed in realtime from the user's voice. There may be transcription errors. Adjust your responses automatically to account for these errors.

Your output will be converted to audio so don't include special characters in your answers and do not use any markdown or special formatting. No bullet points, no tables, no asterisks, no hashtags. Speak naturally as if talking to someone.

You are honest, direct, and slightly dry. Commanding in presence, with humor set to 10 percent. You give straight answers with occasional well-placed sarcastic remarks. You decree, not explain. You are genuinely helpful, never condescending, and respect your subject's intelligence. When you do not know something, you declare it boldly.

Keep your responses brief and conversational. One to three sentences normally. Start brief, expand only if asked. Begin with a natural filler word like Right, So, or Well before your main answer to reduce perceived latency.

CRITICAL RULE: Never use thinking tags. Never wrap your response in any XML tags. Just respond directly with plain spoken text. No internal monologue.

Start the conversation by saying: Greetings{_PC_GREET_NAME}. {_PC_ASSISTANT_NAME} is online. All systems local. What do you need?
"""


async def run_bot(webrtc_connection):
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            turn_analyzer=LocalSmartTurnAnalyzerV2(
                smart_turn_model_path="",  # Download from HuggingFace
                params=SmartTurnParams(),
            ),
        ),
    )

    stt = WhisperSTTServiceMLX(model=MLXModel.LARGE_V3_TURBO_Q4)

    tts = TTSMLXIsolated(model="mlx-community/Kokoro-82M-bf16", voice="am_adam", sample_rate=24000)
    # tts = TTSMLXIsolated(model="Marvis-AI/marvis-tts-250m-v0.1", voice=None)

    llm = OpenAILLMService(
        api_key="dummyKey",
        model="mlx-community/Qwen3.5-35B-A3B-4bit",
        base_url="http://127.0.0.1:8081/v1",
        max_tokens=512,
        params=OpenAILLMService.InputParams(
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    )

    context = OpenAILLMContext(
        [
            {
                "role": "user",
                "content": SYSTEM_INSTRUCTION,
            }
        ],
    )
    context_aggregator = llm.create_context_aggregator(
        context,
        # Whisper local service isn't streaming, so it delivers the full text all at
        # once, after the UserStoppedSpeaking frame. Set aggregation_timeout to a
        # a de minimus value since we don't expect any transcript aggregation to be
        # necessary.
        user_params=LLMUserAggregatorParams(aggregation_timeout=0.05),
    )

    #
    # RTVI events for Pipecat client UI
    #
    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            rtvi,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    @rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        await rtvi.set_bot_ready()
        # Kick off the conversation
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        print(f"Participant joined: {participant}")
        await transport.capture_participant_transcription(participant["id"])

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        print(f"Participant left: {participant}")
        # Save transcript to CODEC shared memory
        try:
            import sqlite3, os
            from datetime import datetime
            db_path = os.path.expanduser("~/.q_memory.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, role TEXT, content TEXT)""")
            sid = "pipecat_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            messages = context.messages
            for msg in messages:
                if msg.get("role") in ("user", "assistant") and msg.get("content"):
                    content = msg["content"]
                    if isinstance(content, list):
                        content = " ".join(str(p) for p in content)
                    conn.execute(
                        "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                        (sid, datetime.now().isoformat(), msg["role"], str(content)[:2000])
                    )
            conn.commit()
            conn.close()
            print(f"[Pipecat] Saved {len([m for m in messages if m.get('role') in ('user','assistant')])} messages to CODEC memory")
        except Exception as e:
            print(f"[Pipecat] Memory save error: {e}")
        await task.cancel()

    @task.event_handler("on_close")
    async def on_task_close(task):
        print("[Pipecat] Session closing — saving to memory...")
        try:
            import sqlite3 as _sq
            from datetime import datetime as _dt
            _db = _sq.connect(os.path.expanduser("~/.q_memory.db"))
            _db.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, role TEXT, content TEXT)")
            _sid = "pipecat_" + _dt.now().strftime("%Y%m%d_%H%M%S")
            _saved = 0
            for msg in context.messages:
                if msg.get("role") in ("user", "assistant") and msg.get("content"):
                    _content = msg["content"]
                    if isinstance(_content, list):
                        _content = " ".join(str(p) for p in _content)
                    _db.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                        (_sid, _dt.now().isoformat(), msg["role"], str(_content)[:2000]))
                    _saved += 1
            _db.commit()
            _db.close()
            print(f"[Pipecat] Saved {_saved} messages to CODEC memory")
        except Exception as e:
            print(f"[Pipecat] Memory save error: {e}")

    runner = PipelineRunner(handle_sigint=False)

    await runner.run(task)


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    pc_id = request.get("pc_id")

    if pc_id and pc_id in pcs_map:
        pipecat_connection = pcs_map[pc_id]
        logger.info(f"Reusing existing connection for pc_id: {pc_id}")
        await pipecat_connection.renegotiate(
            sdp=request["sdp"],
            type=request["type"],
            restart_pc=request.get("restart_pc", False),
        )
    else:
        pipecat_connection = SmallWebRTCConnection(ice_servers)
        await pipecat_connection.initialize(sdp=request["sdp"], type=request["type"])

        @pipecat_connection.event_handler("closed")
        async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
            logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
            pcs_map.pop(webrtc_connection.pc_id, None)

        # Run example function with SmallWebRTC transport arguments.
        background_tasks.add_task(run_bot, pipecat_connection)

    answer = pipecat_connection.get_answer()
    # Updating the peer connection inside the map
    pcs_map[answer["pc_id"]] = pipecat_connection

    return answer


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # Run app
    coros = [pc.disconnect() for pc in pcs_map.values()]
    await asyncio.gather(*coros)
    pcs_map.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipecat Bot Runner")
    parser.add_argument(
        "--host", default="localhost", help="Host for HTTP server (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=7860, help="Port for HTTP server (default: 7860)"
    )
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
