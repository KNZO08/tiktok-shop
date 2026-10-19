"""Reconnaissance vocale dans un processus séparé : python -m pipeline.stt_cli audio.wav modèle sortie.json"""
import json
import sys
import wave

import numpy as np


def main(wav_path, model_name, out_path):
    from faster_whisper import WhisperModel
    with wave.open(wav_path) as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    raw, info = model.transcribe(samples, vad_filter=True, vad_parameters=dict(min_silence_duration_ms=450),
                                 beam_size=5, condition_on_previous_text=False)
    segs = []
    for s in raw:
        text = s.text.strip()
        # on écarte ce que Whisper « invente » sur de la musique ou du bruit
        if not text or (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0) or s.avg_logprob < -1.6:
            continue
        segs.append(dict(start=round(float(s.start), 2), end=round(float(s.end), 2), text=text))
    json.dump(dict(language=info.language, probability=float(info.language_probability), segments=segs),
              open(out_path, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main(*sys.argv[1:4])
