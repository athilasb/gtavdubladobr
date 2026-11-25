"""Reconhece quem fala em cada WAV (só CPU). Rode com o Python do VoiceStudio.

uso: python falantes_classificar.py <pasta_amostra> [saida.json]
Usa ref_michael/ ref_franklin/ ref_trevor/ como vozes conhecidas e neg/ como "outros".
"""
import glob
import json
import os
import sys

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import numpy as np
import soundfile as sf
import torch

torch.set_num_threads(4)
from speechbrain.inference.speaker import EncoderClassifier
from speechbrain.utils.fetching import LocalStrategy

base = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base, "resultado.json")
modelo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados", "ecapa")
enc = EncoderClassifier.from_hparams("speechbrain/spkrec-ecapa-voxceleb", savedir=modelo_dir,
                                     run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)


def emb(p):
    x, sr = sf.read(p, dtype="float32")
    if len(x) < 8000:
        x = np.pad(x, (0, 8000 - len(x)))
    with torch.no_grad():
        e = enc.encode_batch(torch.from_numpy(x)[None])[0, 0].numpy()
    return e / (np.linalg.norm(e) + 1e-9)


def pasta(n):
    return sorted(glob.glob(os.path.join(base, n, "*.wav")))


refs = {}
for nome in ("michael", "franklin", "trevor"):
    fs = pasta("ref_" + nome)
    E = np.stack([emb(f) for f in fs])
    refs[nome] = E
neg = np.stack([emb(f) for f in pasta("neg")])
cent = {k: v.mean(0) / np.linalg.norm(v.mean(0)) for k, v in refs.items()}


def score(e):
    return {k: float(e @ c) for k, c in cent.items()}


# calibração: positivos (deixa-um-fora aproximado) vs pedestres
pos = [max(score(e).values()) for v in refs.values() for e in v]
negs = [max(score(e).values()) for e in neg]
print("positivos: med %.3f p10 %.3f | pedestres: med %.3f p90 %.3f" % (
    np.median(pos), np.percentile(pos, 10), np.median(negs), np.percentile(negs, 90)))
lim = float((np.percentile(pos, 10) + np.percentile(negs, 95)) / 2)
print("limite", round(lim, 3))

res = {}
for i, f in enumerate(pasta("ss")):
    s = score(emb(f))
    k = max(s, key=s.get)
    res[os.path.splitext(os.path.basename(f))[0].replace("~", "/")] = [k if s[k] >= lim else "outro", round(s[k], 3)]
    if i % 200 == 0:
        print(i, flush=True)
from collections import Counter
print(Counter(v[0] for v in res.values()))
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"limite": lim, "res": res}, fh, indent=1)
