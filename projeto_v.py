"""Projeto do Dublador GTA V: índice das falas, suas substituições e aplicação no jogo."""
import glob
import json
import os
import pickle
import shutil
import time

import numpy as np
import soundfile as sf

import gtav_awc as A
import gtav_rpf as R

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME = r"D:\SteamLibrary\steamapps\common\Grand Theft Auto V"
INDEX_VER = 2

DESC = {
    "SS_": "Diálogos das missões (conversas com roteiro)",
    "CUTSCENE_MASTERED_ONLY": "Cutscenes (fala + música juntas, só ouvir/exportar)",
    "S_FULL_AMB_M": "Falas de pedestres homens (completas)",
    "S_FULL_AMB_F": "Falas de pedestres mulheres (completas)",
    "S_FULL_GAN": "Falas de gangues (completas)",
    "S_FULL_SER": "Falas de serviços: polícia, médicos, lojistas (completas)",
    "S_MINI_AMB": "Falas curtas de pedestres",
    "S_MINI_GAN": "Falas curtas de gangues",
    "S_MINI_SER": "Falas curtas de serviços",
    "S_MISC": "Falas avulsas de personagens e anúncios",
    "PAIN": "Gemidos, respiração, dor",
    "PROLOGUE": "Prólogo",
    "POLICE_SCANNER": "Rádio da polícia",
    "SCRIPT": "Sons de script",
    "STREAMS": "Áudios em stream (vários)",
    "RADIO_": "Rádio (músicas e locutores)",
    "dlc": "DLC",
}


def descricao(rel):
    nome = os.path.splitext(os.path.basename(rel))[0]
    if rel.lower().startswith("update"):
        return "DLC " + rel.replace("\\", "/").split("/")[-2]
    for k, v in DESC.items():
        if nome.upper().startswith(k.upper()):
            return v
    return "Efeitos sonoros"


def ordem(rel):
    n = os.path.basename(rel).upper()
    pri = ["SS_", "CUTSCENE", "S_FULL", "S_MINI", "S_MISC", "PROLOGUE", "PAIN", "POLICE", "RADIO"]
    if rel.lower().startswith("update"):
        return (len(pri) + 1, n)
    for i, p in enumerate(pri):
        if n.startswith(p):
            return (i, n)
    return (len(pri), n)


def fala_key(rel, awc, sid):
    return f"{rel}#{awc}#{sid:08X}"


def split_key(key):
    rel, awc, sid = key.split("#")
    return rel, awc, int(sid, 16)


def nome_curto(key):
    rel, awc, sid = split_key(key)
    pk = os.path.splitext(os.path.basename(rel))[0]
    if rel.lower().startswith("update"):
        pk = rel.replace("\\", "/").split("/")[-2]
    return f"{pk}/{os.path.splitext(os.path.basename(awc))[0]}/{sid:08X}"


# ---------------------------------------------------------------- áudio
def to_float(pcm16):
    return np.asarray(pcm16, np.float32) / 32768.0


def to_mono(x):
    return x.mean(axis=1) if x.ndim == 2 else x


def resample(x, sr_from, sr_to):
    if sr_from == sr_to or len(x) == 0:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(x, sr_to // g, sr_from // g, axis=0).astype(np.float32)


def trim_silence(x, sr, thresh=0.01, pad=0.05):
    m = np.abs(x)
    idx = np.where(m > thresh)[0]
    if len(idx) == 0:
        return x
    p = int(pad * sr)
    return x[max(0, idx[0] - p):min(len(x), idx[-1] + p)]


def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def preparar(x, sr, orig_pcm16, orig_rate, trim=True, same_volume=True):
    """Converte sua gravação para o formato da fala original. Devolve (int16, taxa, avisos)."""
    msgs = []
    x = to_mono(np.asarray(x, np.float32))
    x = resample(x, sr, orig_rate)
    if trim:
        x = trim_silence(x, orig_rate)
    if same_volume:
        ref = rms(trim_silence(to_float(orig_pcm16), orig_rate))
        r = rms(x)
        if r > 1e-6 and ref > 1e-6:
            x = x * (ref / r)
    peak = float(np.max(np.abs(x))) if len(x) else 0
    if peak > 0.99:
        x = x * (0.99 / peak)
    d_new, d_old = len(x) / orig_rate, len(orig_pcm16) / orig_rate
    if d_old > 0 and d_new > d_old * 1.6 + 0.5:
        msgs.append(f"Sua versão tem {d_new:.1f}s e a original {d_old:.1f}s. Funciona, mas a boca do "
                    f"personagem pode terminar antes.")
    return (np.clip(x, -1, 1) * 32767).astype(np.int16), orig_rate, msgs


# ---------------------------------------------------------------- projeto
class Projeto:
    def __init__(self, base=BASE):
        self.base = base
        self.dir = os.path.join(base, "projeto")
        self.dir_subs = os.path.join(self.dir, "substituicoes")
        self.backup = os.path.join(base, "backup_original")
        os.makedirs(self.dir_subs, exist_ok=True)
        self.cfg_path = os.path.join(self.dir, "config.json")
        self.notes_path = os.path.join(self.dir, "textos.json")
        self.aplic_path = os.path.join(self.dir, "aplicados.json")
        self.cfg = self._load(self.cfg_path, {"game_dir": DEFAULT_GAME, "trim": True, "volume": True,
                                              "modo": "auto"})
        self.notes = self._load(self.notes_path, {})
        self.aplicados = self._load(self.aplic_path, {})
        self._k = None
        self._rpfs = {}

    @staticmethod
    def _load(p, default):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return {**default, **d}
        except Exception:
            return dict(default)

    def save(self):
        for p, d in ((self.cfg_path, self.cfg), (self.notes_path, self.notes), (self.aplic_path, self.aplicados)):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)

    @property
    def game(self):
        return self.cfg["game_dir"]

    @property
    def chaves(self):
        if self._k is None:
            self._k = R.Chaves.carregar(os.path.join(self.game, "GTA5.exe"))
        return self._k

    # ---- onde ler o original de cada pacote
    def caminho_original(self, rel):
        b = os.path.join(self.backup, rel)
        if os.path.exists(b):
            return b
        return os.path.join(self.game, rel)

    def rpf(self, rel):
        p = self.caminho_original(rel)
        st = os.stat(p)
        k = (rel, p, st.st_size, st.st_mtime)
        if k not in self._rpfs:
            self._rpfs = {kk: v for kk, v in self._rpfs.items() if kk[0] != rel}
            self._rpfs[k] = R.Rpf(p, self.chaves, nome=os.path.basename(rel))
        return self._rpfs[k]

    def entrada(self, rel, awc):
        r = self.rpf(rel)
        for e in r.entradas:
            if e.tipo != "dir" and e.caminho == awc:
                return r, e
        raise KeyError(awc)

    def awc(self, rel, awc):
        r, e = self.entrada(rel, awc)
        return A.Awc(r.ler(e), self.chaves.awc_key)

    def pcm_original(self, key):
        rel, awc, sid = split_key(key)
        return self.awc(rel, awc).pcm(sid)

    # ---- índice
    def pacotes(self):
        g = self.game
        out = []
        for p in sorted(glob.glob(os.path.join(g, "x64", "audio", "sfx", "*.rpf"))):
            out.append(os.path.relpath(p, g).replace("\\", "/"))
        for p in sorted(glob.glob(os.path.join(g, "update", "x64", "dlcpacks", "*", "dlc.rpf"))):
            out.append(os.path.relpath(p, g).replace("\\", "/"))
        return out

    def indice(self, progresso=None):
        """{rel: [(awc, [(id, samples, rate, codec, multi), ...]), ...]} (com cache)."""
        cache_p = os.path.join(self.dir, "indice.pkl")
        try:
            with open(cache_p, "rb") as f:
                cache = pickle.load(f)
            cache = {k.replace("\\", "/"): v for k, v in cache.items()}
            if cache.get("ver") != INDEX_VER:
                cache = {"ver": INDEX_VER}
        except Exception:
            cache = {"ver": INDEX_VER}
        pk = self.pacotes()
        out = {}
        mudou = False
        for i, rel in enumerate(pk):
            p = self.caminho_original(rel)
            st = os.stat(p)
            sig = (st.st_size, int(st.st_mtime))
            c = cache.get(rel)
            if c and c[0] == sig:
                if c[1]:
                    out[rel] = c[1]
                continue
            if progresso:
                progresso(i / len(pk), f"Lendo {rel} ...")
            lista = []
            try:
                r = R.Rpf(p, self.chaves, nome=os.path.basename(rel))
                L = R.Leitor(r)
                for e in r.arquivos():
                    if not e.nome.lower().endswith(".awc"):
                        continue
                    if rel.lower().startswith("update") and "/sfx/" not in "/" + e.caminho.lower():
                        continue
                    try:
                        sons = A.indice(lambda o, n, e=e: L.ler(e, o, n), self.chaves.awc_key)
                    except Exception:
                        continue
                    if sons:
                        lista.append((e.caminho, sons))
                L.fechar()
            except Exception as ex:
                print("erro", rel, ex)
            cache[rel] = (sig, lista)
            mudou = True
            if lista:
                out[rel] = lista
        if mudou:
            with open(cache_p, "wb") as f:
                pickle.dump(cache, f)
        return dict(sorted(out.items(), key=lambda kv: ordem(kv[0])))

    # ---- substituições
    def sub_path(self, key):
        return os.path.join(self.dir_subs, key.replace("/", "~").replace("\\", "~") + ".wav")

    def key_from_file(self, fname):
        return os.path.splitext(os.path.basename(fname))[0].replace("~", "/")

    def has(self, key):
        return os.path.exists(self.sub_path(key))

    def remove(self, key):
        if self.has(key):
            os.remove(self.sub_path(key))

    def replaced_keys(self):
        return {self.key_from_file(n) for n in os.listdir(self.dir_subs) if n.lower().endswith(".wav")}

    def save_sub(self, key, pcm16, rate):
        sf.write(self.sub_path(key), pcm16, int(rate), subtype="PCM_16")

    # ---- modo de aplicação
    def tem_openiv(self):
        g = self.game
        return os.path.exists(os.path.join(g, "OpenIV.asi")) or os.path.exists(os.path.join(g, "scripts", "OpenIV.asi"))

    def modo(self):
        m = self.cfg.get("modo", "auto")
        if m == "auto":
            return "mods" if self.tem_openiv() else "direto"
        return m

    # ---- aplicar / restaurar
    def aplicar(self, log=print, progresso=lambda f, m: None):
        modo = self.modo()
        subs = sorted(self.replaced_keys())
        por_pacote = {}
        for key in subs:
            rel, awc, sid = split_key(key)
            por_pacote.setdefault(rel, {}).setdefault(awc, []).append((sid, key))
        # pacotes aplicados antes que não têm mais substituições: restaura
        for rel in list(self.aplicados):
            if rel not in por_pacote or self.aplicados[rel] != modo:
                self._restaurar_pacote(rel, log)
        total = len(por_pacote)
        avisos = []
        for i, (rel, awcs) in enumerate(por_pacote.items()):
            progresso(i / max(total, 1), f"Montando {rel} ...")
            if modo == "direto":
                self._fazer_backup(rel, log)
            r = self.rpf(rel)
            trocas = {}
            for awc, lst in awcs.items():
                _, e = self.entrada(rel, awc)
                a = A.Awc(r.ler(e), self.chaves.awc_key)
                for sid, key in lst:
                    x, sr = sf.read(self.sub_path(key), dtype="int16")
                    if x.ndim == 2:
                        x = x.mean(axis=1).astype(np.int16)
                    try:
                        a.trocar(sid, x, sr)
                    except Exception as ex:
                        avisos.append(f"{nome_curto(key)}: {ex}")
                trocas[awc] = a.salvar()
            if modo == "mods":
                dest = os.path.join(self.game, "mods", rel)
                R.gravar_rpf(dest, r, trocas, encryption=R.ENC_OPEN)
            else:
                dest = os.path.join(self.game, rel)
                R.gravar_rpf_direto(dest, r, trocas)
            self.aplicados[rel] = modo
            self.save()
            log(f"{rel}: {sum(len(v) for v in awcs.values())} falas trocadas ({modo}).")
        progresso(1, "Pronto.")
        self._rpfs.clear()
        return modo, len(subs), avisos

    def _fazer_backup(self, rel, log):
        b = os.path.join(self.backup, rel)
        if not os.path.exists(b):
            os.makedirs(os.path.dirname(b), exist_ok=True)
            log(f"Backup de {rel} ...")
            shutil.copy2(os.path.join(self.game, rel), b + ".tmp")
            os.replace(b + ".tmp", b)

    def _restaurar_pacote(self, rel, log):
        modo = self.aplicados.get(rel)
        if modo == "mods":
            p = os.path.join(self.game, "mods", rel)
            if os.path.exists(p):
                os.remove(p)
        elif modo == "direto":
            b = os.path.join(self.backup, rel)
            if os.path.exists(b):
                shutil.copy2(b, os.path.join(self.game, rel) + ".tmp")
                os.replace(os.path.join(self.game, rel) + ".tmp", os.path.join(self.game, rel))
                os.remove(b)
        self.aplicados.pop(rel, None)
        self._rpfs.clear()
        self.save()
        log(f"{rel}: original restaurado.")

    def restaurar_tudo(self, log=print):
        for rel in list(self.aplicados):
            self._restaurar_pacote(rel, log)
        # backups que sobraram (por segurança)
        for root, _, files in os.walk(self.backup):
            for n in files:
                if n.endswith(".rpf"):
                    b = os.path.join(root, n)
                    rel = os.path.relpath(b, self.backup)
                    shutil.copy2(b, os.path.join(self.game, rel))
                    os.remove(b)
                    log(f"{rel}: original restaurado do backup.")
