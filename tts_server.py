#!/usr/bin/env python3
"""Local XTTS voice clone server for StrokeGuard."""

import io
import os
import torch
from flask import Flask, request, Response, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Reference audio for voice cloning
REF_AUDIO = "../auton_cloud/assets/voice_ref.wav"
MODEL = None

def load_model():
    global MODEL
    from TTS.api import TTS
    print("Loading XTTS on CPU...")
    MODEL = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
    print("Model loaded!")
    return MODEL

@app.route("/health")
def health():
    return jsonify({"status": "ok", "model_loaded": MODEL is not None})

@app.route("/synth", methods=["POST"])
def synth():
    """Synthesize speech with voice cloning.
    POST JSON: {"text": "Hello world"}
    Returns: audio/wav
    """
    data = request.get_json()
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Generate speech with voice cloning
    wav = MODEL.tts(
        text=text,
        speaker_wav=REF_AUDIO,
        language="en"
    )

    # Convert to WAV bytes
    import scipy.io.wavfile as wavfile
    import numpy as np

    buf = io.BytesIO()
    wav_np = np.array(wav)
    wavfile.write(buf, 24000, (wav_np * 32767).astype(np.int16))
    buf.seek(0)

    return Response(buf.read(), mimetype="audio/wav")

@app.route("/synth_stream", methods=["POST"])
def synth_stream():
    """Stream synthesis for real-time playback."""
    data = request.get_json()
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "No text provided"}), 400

    def generate():
        wav = MODEL.tts(
            text=text,
            speaker_wav=REF_AUDIO,
            language="en"
        )
        import numpy as np
        wav_np = np.array(wav)
        yield (wav_np * 32767).astype("<i2").tobytes()

    return Response(generate(), mimetype="application/octet-stream")

if __name__ == "__main__":
    load_model()
    print("TTS server ready on http://localhost:5002")
    app.run(host="0.0.0.0", port=5002, threaded=True)
