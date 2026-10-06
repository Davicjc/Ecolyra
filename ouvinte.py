"""Ouvinte do Tradutor de Letras — processo separado que escuta o som do PC.

Duas "orelhas":
  • Shazam (impressão digital do áudio): diz QUAL música é e EM QUE SEGUNDO da música de
    estúdio o trecho está — rápido (~0,3 s), funciona até em parte instrumental.
  • Whisper (transcrição, roda no PC): diz QUAIS PALAVRAS foram cantadas — reserva para o que
    o Shazam não conhece (ao vivo, cover, música rara) e para conferir a sincronia.

Roda isolado porque o ctranslate2 (faster-whisper) e o winrt do app principal não podem
ficar no mesmo processo (o modelo dá access violation ao carregar).

Protocolo (uma linha JSON por vez):
  entrada:  {"ouvir": 8, "shazam": true, "whisper": false, "idioma": null}
  saída:    {"t_ini": <relógio do início do áudio>, "shazam": {...} | null, "palavras": [[ini, fim, "palavra"], ...]}
            {"erro": "..."}  /  {"pronto": true} quando carregou
"""
import asyncio
import io
import json
import sys
import time
import wave
import warnings

warnings.filterwarnings("ignore")


def enviar(d):
    sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def gravar_saida(pa, np, seg):
    """Grava o que está saindo na saída de som padrão (loopback WASAPI). → (mono float32, relógio do início, taxa)"""
    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        saida = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        disp = next((d for d in p.get_loopback_device_info_generator() if saida["name"] in d["name"]), None)
        if disp is None:
            raise RuntimeError("sem loopback para " + saida["name"])
        taxa, canais = int(disp["defaultSampleRate"]), max(1, disp["maxInputChannels"])
        blocos = []
        st = p.open(format=pa.paFloat32, channels=canais, rate=taxa, input=True,
                    input_device_index=disp["index"], frames_per_buffer=1024)
        try:
            st.read(1024, exception_on_overflow=False)  # descarta o que estava no buffer
            falta = int(taxa * seg)
            while falta > 0:
                n = min(1024, falta)
                blocos.append(st.read(n, exception_on_overflow=False))
                falta -= n
        finally:
            st.stop_stream()
            st.close()
        dados = np.frombuffer(b"".join(blocos), dtype=np.float32).reshape(-1, canais).mean(axis=1)
        # o loopback só entrega quando tem som; o início real é fim − duração
        t_ini = time.time() - len(dados) / taxa
        return dados, t_ini, taxa
    finally:
        p.terminate()


def reamostrar(np, audio, taxa):
    if taxa == 16000:
        return audio.astype(np.float32)
    n = int(len(audio) * 16000 / taxa)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def wav16(np, a16):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(a16, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


def shazam(loop, Shazam, np, a16):
    try:
        r = loop.run_until_complete(asyncio.wait_for(Shazam().recognize(wav16(np, a16)), 12))
    except Exception as e:
        return {"erro": repr(e)}
    tr, ms = r.get("track") or {}, r.get("matches") or []
    if not tr or not ms:
        return None
    return {"titulo": tr.get("title", ""), "artista": tr.get("subtitle", ""),
            "offset": float(ms[0].get("offset") or 0), "id": tr.get("key", "")}


def main():
    import numpy as np
    import pyaudiowpatch as pa
    modelo = None
    try:
        from shazamio import Shazam
    except Exception:
        Shazam = None
    loop = asyncio.new_event_loop()
    enviar({"pronto": True, "shazam": Shazam is not None})
    for linha in sys.stdin:
        try:
            pedido = json.loads(linha)
            audio, t_ini, taxa = gravar_saida(pa, np, float(pedido.get("ouvir", 8)))
            a16 = reamostrar(np, audio, taxa)
            resp = {"t_ini": t_ini, "shazam": None, "palavras": []}
            if float(np.sqrt((a16 ** 2).mean())) < 0.003:
                resp["silencio"] = True
                enviar(resp)
                continue
            if pedido.get("shazam", True) and Shazam:
                resp["shazam"] = shazam(loop, Shazam, np, a16)
            if pedido.get("whisper"):
                if modelo is None:  # só carrega a IA (≈350 MB) se precisar
                    from faster_whisper import WhisperModel
                    modelo = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
                segs, info = modelo.transcribe(a16, word_timestamps=True, beam_size=1, vad_filter=False,
                                               condition_on_previous_text=False,
                                               language=pedido.get("idioma") or None)
                resp["palavras"] = [[round(w.start, 3), round(w.end, 3), w.word] for s in segs for w in (s.words or [])]
                resp["idioma"] = info.language
            enviar(resp)
        except Exception as e:
            enviar({"erro": repr(e)})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        enviar({"erro": repr(e)})
