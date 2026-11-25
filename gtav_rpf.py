"""
Leitura e escrita de arquivos RPF7 do GTA V (PC, Legacy).

Baseado no CodeWalker (MIT, dexyfex / Neodymium):
- a chave AES fica no GTA5.exe (achada pelo SHA1 conhecido);
- as chaves/tabelas "NG" vêm do magic.dat do CodeWalker, embaralhado com a chave AES;
- o índice (TOC) do RPF é criptografado com NG (chave escolhida pelo nome + tamanho do arquivo).

Para gravar, criamos um RPF novo com o índice sem criptografia ("OPEN", estilo OpenIV),
que é o que o OpenIV.asi lê da pasta mods. Se pedir, também grava com AES.
"""
import hashlib
import os
import pickle
import shutil
import struct
import zlib

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
MAGIC_DAT = os.path.join(AQUI, "dados", "magic.dat")
MAGIC_URL = "https://raw.githubusercontent.com/dexyfex/CodeWalker/master/CodeWalker.Core/Resources/magic.dat"
CACHE_CHAVES = os.path.join(AQUI, "dados", "chaves.cache")

AES_KEY_SHA1 = bytes([0xA0, 0x79, 0x61, 0x28, 0xA7, 0x75, 0x72, 0x0A, 0xC2, 0x04,
                      0xD9, 0x81, 0x9F, 0x68, 0xC1, 0x72, 0xE3, 0x95, 0x2C, 0x6D])

ENC_NONE = 0
ENC_OPEN = 0x4E45504F
ENC_AES = 0x0FFFFFF9
ENC_NG = 0x0FEFFFFF
RPF7 = 0x52504637


# ---------------------------------------------------------------- chaves
class _DotNetRandom:
    """System.Random (algoritmo antigo do .NET, com semente)."""

    def __init__(self, seed):
        MBIG, MSEED = 2147483647, 161803398

        def i32(v):
            v &= 0xFFFFFFFF
            return v - 0x100000000 if v >= 0x80000000 else v
        sub = 2147483647 if seed == -2147483648 else abs(seed)
        mj = i32(MSEED - sub)
        sa = [0] * 56
        sa[55] = mj
        mk = 1
        for i in range(1, 55):
            ii = (21 * i) % 55
            sa[ii] = mk
            mk = i32(mj - mk)
            if mk < 0:
                mk = i32(mk + MBIG)
            mj = sa[ii]
        for _ in range(4):
            for i in range(1, 56):
                sa[i] = i32(sa[i] - sa[1 + (i + 30) % 55])
                if sa[i] < 0:
                    sa[i] = i32(sa[i] + MBIG)
        self.sa, self.inext, self.inextp = sa, 0, 21

    def next_bytes(self, n):
        MBIG = 2147483647
        sa, a, b = self.sa, self.inext, self.inextp
        out = bytearray(n)
        for i in range(n):
            a += 1
            if a >= 56:
                a = 1
            b += 1
            if b >= 56:
                b = 1
            r = (sa[a] - sa[b]) & 0xFFFFFFFF
            if r >= 0x80000000:
                r -= 0x100000000
            if r == MBIG:
                r -= 1
            if r < 0:
                r += MBIG
            sa[a] = r
            out[i] = r & 0xFF
        self.inext, self.inextp = a, b
        return bytes(out)


def _jenk(data: bytes) -> int:
    h = 0
    for c in data:
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h


def _achar_aes(exe_path):
    data = open(exe_path, "rb").read()
    sha1 = hashlib.sha1
    mv = memoryview(data)
    for pos in range(0, len(data) - 32, 8):
        if sha1(mv[pos:pos + 32]).digest() == AES_KEY_SHA1:
            return bytes(mv[pos:pos + 32])
    raise RuntimeError("Não achei a chave AES no GTA5.exe.")


def _aes_ecb(key, data, decrypt=True):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    n = len(data) - len(data) % 16
    if n == 0:
        return bytes(data)
    c = Cipher(algorithms.AES(key), modes.ECB())
    op = c.decryptor() if decrypt else c.encryptor()
    return op.update(bytes(data[:n])) + op.finalize() + bytes(data[n:])


class Chaves:
    def __init__(self, aes, ng_keys, ng_tables, lut, awc_key):
        self.aes = aes
        self.ng_keys = ng_keys          # (101, 68) uint32
        self.ng_tables = ng_tables      # (17, 16, 256) uint32
        self.lut = lut                  # 256 bytes
        self.awc_key = awc_key          # 4 uint32

    @classmethod
    def carregar(cls, exe_path):
        if os.path.exists(CACHE_CHAVES):
            with open(CACHE_CHAVES, "rb") as f:
                return pickle.load(f)
        aes = _achar_aes(exe_path)
        if not os.path.exists(MAGIC_DAT):
            import urllib.request
            os.makedirs(os.path.dirname(MAGIC_DAT), exist_ok=True)
            urllib.request.urlretrieve(MAGIC_URL, MAGIC_DAT)
        m = open(MAGIC_DAT, "rb").read()
        seed = _jenk(aes)
        if seed >= 0x80000000:
            seed -= 0x100000000
        rnd = _DotNetRandom(seed)
        n = len(m)
        rbs = [np.frombuffer(rnd.next_bytes(n), np.uint8).astype(np.int32) for _ in range(4)]
        db = ((np.frombuffer(m, np.uint8).astype(np.int32) - rbs[0] - rbs[1] - rbs[2] - rbs[3]) & 0xFF).astype(np.uint8).tobytes()
        db = _aes_ecb(aes, db)
        b = zlib.decompressobj(-15).decompress(db)
        p = 0
        ng_keys = np.frombuffer(b[p:p + 27472], "<u4").reshape(101, 68).copy(); p += 27472
        ng_tables = np.frombuffer(b[p:p + 278528], "<u4").reshape(17, 16, 256).copy(); p += 278528
        lut = b[p:p + 256]; p += 256
        awc = list(struct.unpack("<4I", b[p:p + 16]))
        k = cls(aes, ng_keys, ng_tables, lut, awc)
        os.makedirs(os.path.dirname(CACHE_CHAVES), exist_ok=True)
        with open(CACHE_CHAVES, "wb") as f:
            pickle.dump(k, f)
        return k

    # -- NG
    def hash_nome(self, text):
        r = 0
        lut = self.lut
        for ch in text:
            t = (1025 * (lut[ord(ch) & 0xFF] + r)) & 0xFFFFFFFF
            r = ((t >> 6) ^ t) & 0xFFFFFFFF
        a = (9 * r) & 0xFFFFFFFF
        return (32769 * ((a >> 11) ^ a)) & 0xFFFFFFFF

    def ng_key(self, name, length):
        idx = (self.hash_nome(name) + length + 61) % 0x65
        return self.ng_keys[idx]

    def decrypt_ng(self, data, name, length, key_idx=None):
        if key_idx is None:
            key_idx = (self.hash_nome(name) + length + 61) % 0x65
        key = self.ng_keys[key_idx].reshape(17, 4)
        n = len(data) // 16
        if n == 0:
            return bytes(data)
        blk = np.frombuffer(bytes(data[:n * 16]), np.uint8).reshape(n, 16)
        T = self.ng_tables
        A = [list(range(16))]
        B = [0, 7, 10, 13, 1, 4, 11, 14, 2, 5, 8, 15, 3, 6, 9, 12]

        def rnd(blk, r, order):
            t = T[r]
            out = np.empty((n, 4), np.uint32)
            for w in range(4):
                i0, i1, i2, i3 = order[4 * w:4 * w + 4]
                out[:, w] = (t[i0][blk[:, i0]] ^ t[i1][blk[:, i1]] ^ t[i2][blk[:, i2]]
                             ^ t[i3][blk[:, i3]] ^ key[r][w])
            return out.view(np.uint8).reshape(n, 16)

        ida = A[0]
        blk = rnd(blk, 0, ida)
        blk = rnd(blk, 1, ida)
        for r in range(2, 16):
            blk = rnd(blk, r, B)
        blk = rnd(blk, 16, ida)
        return blk.tobytes() + bytes(data[n * 16:])

    def decrypt_aes(self, data):
        return _aes_ecb(self.aes, data, True)

    def encrypt_aes(self, data):
        return _aes_ecb(self.aes, data, False)


# ---------------------------------------------------------------- RPF
class Entrada:
    __slots__ = ("nome", "caminho", "tipo", "offset", "tamanho", "tam_real", "cripto",
                 "sys_flags", "gfx_flags", "dir_index", "dir_count", "name_off", "raw")

    def __repr__(self):
        return f"<{self.tipo} {self.caminho} off={self.offset} size={self.tamanho}>"


class Rpf:
    """Um RPF7 aberto para leitura (arquivo no disco)."""

    def __init__(self, path, chaves: Chaves, nome=None, start=0, size=None, pai=None, entrada_pai=None):
        self.path = path
        self.k = chaves
        self.nome = nome or os.path.basename(path)
        self.start = start
        self.pai, self.entrada_pai = pai, entrada_pai
        self.tam_arquivo = size if size is not None else os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(start)
            ver, cnt, nlen, enc = struct.unpack("<4I", f.read(16))
            if ver != RPF7:
                raise ValueError(f"{path}: não é RPF7")
            ent = f.read(cnt * 16)
            names = f.read(nlen)
        self.encryption = enc
        if enc == ENC_AES:
            ent, names = chaves.decrypt_aes(ent), chaves.decrypt_aes(names)
        elif enc not in (ENC_NONE, ENC_OPEN):
            ki = (chaves.hash_nome(self.nome) + (self.tam_arquivo & 0xFFFFFFFF) + 61) % 0x65
            ok = lambda i: struct.unpack_from("<I", chaves.decrypt_ng(ent[:16], "", 0, i), 4)[0] == 0x7FFFFF00
            if not ok(ki):
                ki = next((i for i in range(0x65) if ok(i)), ki)
            ent = chaves.decrypt_ng(ent, "", 0, ki)
            names = chaves.decrypt_ng(names, "", 0, ki)
        self.names_raw = names
        self.entradas = []
        for i in range(cnt):
            raw = ent[i * 16:(i + 1) * 16]
            e = Entrada()
            e.raw = raw
            x = struct.unpack_from("<I", raw, 4)[0]
            if x == 0x7FFFFF00:
                e.tipo = "dir"
                e.name_off, _, e.dir_index, e.dir_count = struct.unpack("<4I", raw)
            elif x & 0x80000000 == 0:
                e.tipo = "bin"
                q, e.tam_real, e.cripto = struct.unpack("<QII", raw)
                e.name_off = q & 0xFFFF
                e.tamanho = (q >> 16) & 0xFFFFFF   # comprimido (0 = sem compressão)
                e.offset = (q >> 40) & 0xFFFFFF
            else:
                e.tipo = "res"
                e.name_off = struct.unpack_from("<H", raw, 0)[0]
                e.tamanho = int.from_bytes(raw[2:5], "little")
                e.offset = int.from_bytes(raw[5:8], "little") & 0x7FFFFF
                e.sys_flags, e.gfx_flags = struct.unpack_from("<II", raw, 8)
                e.cripto = 0
                e.tam_real = None
            end = names.index(b"\0", e.name_off)
            e.nome = names[e.name_off:end].decode("latin-1")
            self.entradas.append(e)
        # caminhos
        self.entradas[0].caminho = ""
        stack = [self.entradas[0]]
        while stack:
            d = stack.pop()
            for j in range(d.dir_index, d.dir_index + d.dir_count):
                c = self.entradas[j]
                c.caminho = (d.caminho + "/" + c.nome) if d.caminho else c.nome
                if c.tipo == "dir":
                    stack.append(c)
        if any(e.tipo == "res" and e.tamanho == 0xFFFFFF for e in self.entradas):
            with open(path, "rb") as f:
                for e in self.entradas:
                    if e.tipo == "res" and e.tamanho == 0xFFFFFF:
                        f.seek(self.start + e.offset * 512)
                        b = f.read(16)
                        e.tamanho = b[7] | (b[14] << 8) | (b[5] << 16) | (b[2] << 24)

    def sub_rpf(self, e):
        """Abre um .rpf guardado dentro deste."""
        return Rpf(self.path, self.k, e.nome, start=self.start + e.offset * 512,
                   size=self.tamanho_no_disco(e), pai=self, entrada_pai=e)

    def arquivos(self):
        return [e for e in self.entradas if e.tipo != "dir"]

    def tamanho_no_disco(self, e):
        if e.tipo == "bin":
            return e.tamanho if e.tamanho else e.tam_real
        return e.tamanho

    def ler_bruto(self, e):
        with open(self.path, "rb") as f:
            f.seek(self.start + e.offset * 512)
            return f.read(self.tamanho_no_disco(e))

    def ler(self, e):
        """Conteúdo do arquivo já decifrado/descomprimido (só binários)."""
        b = self.ler_bruto(e)
        if e.tipo == "bin":
            if e.cripto:
                if self.encryption == ENC_AES:
                    b = self.k.decrypt_aes(b)
                else:
                    b = self.k.decrypt_ng(b, e.nome, e.tam_real)
            if e.tamanho:
                b = zlib.decompressobj(-15).decompress(b)
        return b


def gravar_rpf(destino, origem: Rpf, trocas: dict, encryption=ENC_OPEN):
    """Grava um RPF novo = origem com os arquivos em `trocas` ({caminho: bytes}) trocados.

    Os arquivos trocados vão sem compressão e sem criptografia. Os outros são copiados
    como estão (bytes brutos), por isso arquivos binários cifrados com NG continuam válidos
    só se o nome/tamanho não mudarem (não mudam).
    """
    ents = origem.entradas
    names = origem.names_raw
    hdr_len = 16 + len(ents) * 16 + len(names)
    pos_blk = (hdr_len + 511) // 512
    novos = []
    blocos = []  # (entrada_idx, bytes ou None, bloco)
    plano = []
    for i, e in enumerate(ents):
        if e.tipo == "dir":
            novos.append(e.raw)
            continue
        if e.caminho in trocas:
            data = trocas[e.caminho]
            if e.tipo != "bin":
                raise ValueError("só sei trocar arquivos binários (awc)")
            off = pos_blk
            raw = struct.pack("<QII", (e.name_off & 0xFFFF) | (0 << 16) | (off << 40), len(data), 0)
        else:
            data = None
            off = pos_blk
            size = origem.tamanho_no_disco(e)
            if e.tipo == "bin":
                q = struct.unpack_from("<Q", e.raw, 0)[0]
                q = (q & 0xFFFFFFFFFF) | (off << 40)
                raw = struct.pack("<Q", q) + e.raw[8:]
            else:
                raw = e.raw[:5] + (off | 0x800000).to_bytes(3, "little") + e.raw[8:]
        n = len(data) if data is not None else origem.tamanho_no_disco(e)
        if off >= 0x7FFFFF:
            raise ValueError("RPF grande demais")
        novos.append(raw)
        plano.append((e, data, off))
        pos_blk += (n + 511) // 512
    ent_bytes = b"".join(novos)
    nm = names
    if encryption == ENC_AES:
        ent_bytes, nm = origem.k.encrypt_aes(ent_bytes), origem.k.encrypt_aes(nm)
    tmp = destino + ".tmp"
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(origem.path, "rb") as src, open(tmp, "wb") as out:
        out.write(struct.pack("<4I", RPF7, len(ents), len(names), encryption))
        out.write(ent_bytes)
        out.write(nm)
        for e, data, off in plano:
            out.seek(off * 512)
            if data is None:
                src.seek(origem.start + e.offset * 512)
                data = src.read(origem.tamanho_no_disco(e))
            out.write(data)
        end = out.tell()
        pad = (-end) % 512
        if pad:
            out.write(b"\0" * pad)
    os.replace(tmp, destino)


class Leitor:
    """Lê pedaços de um arquivo (sem compressão/cripto) dentro de um RPF, mantendo o arquivo aberto."""

    def __init__(self, rpf: Rpf):
        self.rpf = rpf
        self.f = open(rpf.path, "rb")

    def pode_parcial(self, e):
        return e.tipo == "bin" and not e.cripto and not e.tamanho

    def ler(self, e, off=0, n=None):
        if not self.pode_parcial(e):
            b = self.rpf.ler(e)
            return b[off:] if n is None else b[off:off + n]
        tam = e.tam_real
        if n is None:
            n = tam - off
        self.f.seek(self.rpf.start + e.offset * 512 + off)
        return self.f.read(max(0, min(n, tam - off)))

    def fechar(self):
        self.f.close()


# ---------------------------------------------------------------- cifrar NG (inverso por "encontro no meio")
_ORD_A = [list(range(4 * w, 4 * w + 4)) for w in range(4)]
_ORD_B = [[0, 7, 10, 13], [1, 4, 11, 14], [2, 5, 8, 15], [3, 6, 9, 12]]
_INV_CACHE = {}


def _inv_tab(k, r, w, order):
    ck = (id(k), r, w)
    if ck not in _INV_CACHE:
        t = k.ng_tables[r]
        a, b, c, d = order
        L = (t[a][:, None] ^ t[b][None, :]).reshape(-1)          # índice = b0*256 + b1
        Rr = (t[c][:, None] ^ t[d][None, :]).reshape(-1)
        idx = np.argsort(Rr, kind="stable")
        _INV_CACHE[ck] = (L, Rr[idx], idx)
    return _INV_CACHE[ck]


def _inverter(k, r, w, order, y):
    L, Rs, Ridx = _inv_tab(k, r, w, order)
    alvo = L ^ np.uint32(y)
    pos = np.searchsorted(Rs, alvo)
    pos[pos >= len(Rs)] = 0
    hit = np.nonzero(Rs[pos] == alvo)[0]
    if len(hit) != 1:
        raise RuntimeError("falha ao cifrar bloco NG")
    i = int(hit[0])
    j = int(Ridx[pos[i]])
    return i >> 8, i & 0xFF, j >> 8, j & 0xFF


def encrypt_ng(k: "Chaves", data: bytes, key_idx: int) -> bytes:
    key = k.ng_keys[key_idx].reshape(17, 4)
    out = bytearray(data)
    for bo in range(0, len(data) - len(data) % 16, 16):
        buf = list(data[bo:bo + 16])
        for r in [16] + list(range(15, 1, -1)) + [1, 0]:
            order = _ORD_A if r in (0, 1, 16) else _ORD_B
            src = [0] * 16
            for w in range(4):
                y = buf[4 * w] | (buf[4 * w + 1] << 8) | (buf[4 * w + 2] << 16) | (buf[4 * w + 3] << 24)
                y ^= int(key[r][w])
                bs = _inverter(k, r, w, order[w], y)
                for pos_, val in zip(order[w], bs):
                    src[pos_] = val
            buf = src
        out[bo:bo + 16] = bytes(buf)
    return bytes(out)


def chave_ng_idx(rpf: Rpf, ent_bytes: bytes):
    """Descobre qual das 101 chaves NG decifra o índice deste RPF."""
    k = rpf.k
    ki = (k.hash_nome(rpf.nome) + (rpf.tam_arquivo & 0xFFFFFFFF) + 61) % 0x65
    ok = lambda i: struct.unpack_from("<I", k.decrypt_ng(ent_bytes[:16], "", 0, i), 4)[0] == 0x7FFFFF00
    if ok(ki):
        return ki
    return next(i for i in range(0x65) if ok(i))


def gravar_rpf_direto(destino, origem: Rpf, trocas: dict):
    """Cópia do RPF original (NG) com os arquivos trocados colocados no fim.

    Só as entradas trocadas do índice mudam (re-cifradas com a mesma chave NG). O tamanho final
    fica com o mesmo resto da divisão por 101 que o original, para a chave continuar a mesma.
    """
    if origem.start != 0 or origem.encryption != ENC_NG:
        raise ValueError("modo direto só para RPF do jogo (NG)")
    with open(origem.path, "rb") as f:
        hdr = f.read(16)
        cnt = struct.unpack_from("<I", hdr, 4)[0]
        ent_enc = f.read(cnt * 16)
    ki = chave_ng_idx(origem, ent_enc)
    tam0 = origem.tam_arquivo
    tmp = destino + ".tmp"
    shutil.copyfile(origem.path, tmp)
    with open(tmp, "r+b") as out:
        out.seek(0, 2)
        pos = out.tell()
        pos += (-pos) % 512
        novos = {}
        for i, e in enumerate(origem.entradas):
            if e.tipo == "dir" or e.caminho not in trocas:
                continue
            data = trocas[e.caminho]
            blk = pos // 512
            if blk >= 0xFFFFFF:
                raise ValueError("RPF grande demais")
            out.seek(pos)
            out.write(data)
            pos += len(data)
            pos += (-pos) % 512
            raw = struct.pack("<QII", (e.name_off & 0xFFFF) | (blk << 40), len(data), 0)
            novos[i] = raw
        # tamanho final ≡ original (mod 101) e múltiplo de 512
        while pos % 101 != tam0 % 101:
            pos += 512
        out.truncate(pos)
        out.seek(pos - 1)
        out.write(b"\0")
        for i, raw in novos.items():
            out.seek(16 + i * 16)
            out.write(encrypt_ng(origem.k, raw, ki))
    # confere
    chk = Rpf(tmp, origem.k, nome=origem.nome)
    for i in novos:
        e = chk.entradas[i]
        if e.caminho != origem.entradas[i].caminho or chk.ler(e) != trocas[e.caminho]:
            os.remove(tmp)
            raise RuntimeError("conferência do RPF falhou")
    os.replace(tmp, destino)
