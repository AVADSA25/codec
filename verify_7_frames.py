import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Define the 7 core codec product frames/modules to check
frames = [
    'codec_agent',
    'codec_agents',
    'codec_scheduler',
    'codec_memory',
    'codec_marketplace',
    'codec_mcp',
    'codec_keyboard'
]

results = {}
for frame in frames:
    try:
        __import__(frame)
        results[frame] = "OK"
    except Exception as e:
        results[frame] = f"FAIL: {str(e)}"

print("=== 7 Codec Product Frames Verification ===")
for frame, status in results.items():
    print(f"{frame}: {status}")
print("=== Verification Complete ===")
