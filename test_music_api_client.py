import requests
import json
from midi_generator import generate_midi_from_intent

API_URL = "http://127.0.0.1:8000"

# 1. Call /prompt-to-intent to get a structured intent from a prompt
prompt = {"prompt": "A fast happy melody in C major with piano and drums"}
resp = requests.post(f"{API_URL}/prompt-to-intent", json=prompt)
resp.raise_for_status()
intent = resp.json()
print("Intent received from API:")
print(json.dumps(intent, indent=2))

# 2. Generate the MIDI file locally using the midi_generator function
generate_midi_from_intent(intent, "test_output.mid")
print("MIDI file generated locally as test_output.mid using midi_generator.py")
