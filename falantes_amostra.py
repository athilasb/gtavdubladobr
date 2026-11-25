"""Exporta uma amostra de falas para identificar quem fala (Michael, Franklin, Trevor ou outro).

Gera WAV 16 kHz em <saida>/ref_<personagem>/ (falas conhecidas do S_MISC),
<saida>/neg/ (pedestres) e <saida>/ss/ (amostra aleatória dos diálogos das missões).
"""
import os
import random
import sys

import numpy as np
import soundfile as sf

import projeto_v as P

saida = sys.argv[1]
N_SS = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
random.seed(1)
p = P.Projeto()
idx = p.indice()


def exportar(rel, awc, sid, pasta):
    os.makedirs(pasta, exist_ok=True)
    x, sr = p.awc(rel, awc).pcm(sid) if (rel, awc) not in cache else cache[(rel, awc)].pcm(sid)
    x = P.resample(P.to_float(x), sr, 16000)
    sf.write(os.path.join(pasta, P.fala_key(rel, awc, sid).replace("/", "~") + ".wav"), x, 16000)


cache = {}


def awc_obj(rel, awc):
    if (rel, awc) not in cache:
        cache.clear()
        cache[(rel, awc)] = p.awc(rel, awc)
    return cache[(rel, awc)]


misc = "x64/audio/sfx/S_MISC.rpf"
for nome in ("michael", "franklin", "trevor"):
    for awc in (f"{nome}_1_normal.awc", f"{nome}_2_normal.awc"):
        sons = [s for s in dict(idx[misc])[awc] if s[1] / s[2] > 1.2]
        a = awc_obj(misc, awc)
        for s in random.sample(sons, 40):
            exportar(misc, awc, s[0], os.path.join(saida, "ref_" + nome))

for rel in ("x64/audio/sfx/S_FULL_AMB_M.rpf", "x64/audio/sfx/S_FULL_GAN.rpf", "x64/audio/sfx/S_FULL_SER.rpf"):
    for awc, sons in random.sample(idx[rel], 15):
        a = awc_obj(rel, awc)
        for s in random.sample(sons, min(3, len(sons))):
            exportar(rel, awc, s[0], os.path.join(saida, "neg"))

todas = [(rel, awc, s) for rel, awcs in idx.items() if "/SS_" in rel for awc, sons in awcs for s in sons]
amostra = sorted(random.sample(todas, N_SS), key=lambda t: (t[0], t[1]))
for i, (rel, awc, s) in enumerate(amostra):
    awc_obj(rel, awc)
    exportar(rel, awc, s[0], os.path.join(saida, "ss"))
    if i % 200 == 0:
        print(i, flush=True)
print("ok")
