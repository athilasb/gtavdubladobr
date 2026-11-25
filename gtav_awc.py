"""
Leitura/escrita de arquivos AWC do GTA V (baseado no CodeWalker, MIT).

Um AWC tem várias "streams". Nas falas, cada stream é uma fala (id = hash de 29 bits do nome).
Em músicas/cutscenes (flag multicanal) uma stream "fonte" guarda blocos intercalados e as
outras streams são os canais.

Troca de fala: só para streams de canal único. A fala nova vai em PCM 16-bit (codec 0),
na mesma taxa da original, e o pico (peak) é recalculado.
"""
import struct

import numpy as np

try:
    from numba import njit
except Exception:  # sem numba: versão Python pura (lenta)
    njit = None

T_DATA, T_FORMAT, T_ANIM, T_PEAK, T_MID, T_GESTURE = 0x55, 0xFA, 0x5C, 0x36, 0x68, 0x2B
T_GGRAINS, T_GLOOPS, T_MARKERS, T_STREAMFMT, T_SEEK = 0x5A, 0xD9, 0xBD, 0x48, 0xA3
NOMES_CHUNK = {T_DATA: "data", T_FORMAT: "format", T_ANIM: "animation", T_PEAK: "peak", T_MID: "mid",
               T_GESTURE: "gesture", T_GGRAINS: "granulargrains", T_GLOOPS: "granularloops",
               T_MARKERS: "markers", T_STREAMFMT: "streamformat", T_SEEK: "seektable"}
CODEC_PCM, CODEC_ADPCM = 0, 4


def _sort_order(t):
    if t in (T_DATA, T_MID):
        return 3
    if t in (T_MARKERS, T_GGRAINS, T_GLOOPS, T_ANIM, T_GESTURE):
        return 2
    if t == T_SEEK:
        return 1
    return 0


def _align(t):
    if t in (T_DATA, T_MID):
        return 16
    if t in (T_MARKERS, T_GGRAINS, T_GLOOPS):
        return 4
    return 0


# ---------------------------------------------------------------- RSXXTEA
def _u32(x):
    return x & 0xFFFFFFFF


def rsxxtea_decrypt(data: bytes, key):
    n = len(data) // 4
    if n < 2:
        return data
    v = list(struct.unpack(f"<{n}I", data[:n * 4]))
    i = _u32(0x9E3779B9 * (6 + 52 // n))
    b = v[0]
    while True:
        for bi in range(n - 1, -1, -1):
            a = v[(bi if bi > 0 else n) - 1]
            mx = _u32((_u32((a >> 5) ^ _u32(b << 2)) + _u32((b >> 3) ^ _u32(a << 4))))
            mx = mx ^ _u32(_u32(i ^ b) + _u32(key[(bi & 3) ^ ((i >> 2) & 3)] ^ a ^ 0x7B3A207F))
            v[bi] = b = _u32(v[bi] - mx)
        i = _u32(i - 0x9E3779B9)
        if i == 0:
            break
    return struct.pack(f"<{n}I", *v) + data[n * 4:]


def rsxxtea_encrypt(data: bytes, key):
    n = len(data) // 4
    if n < 2:
        return data
    v = list(struct.unpack(f"<{n}I", data[:n * 4]))
    rounds = 6 + 52 // n
    s = 0
    z = v[n - 1]
    while rounds:
        s = _u32(s + 0x9E3779B9)
        e = (s >> 2) & 3
        for p in range(n):
            y = v[(p + 1) % n]
            mx = _u32(_u32((z >> 5) ^ _u32(y << 2)) + _u32((y >> 3) ^ _u32(z << 4)))
            mx = mx ^ _u32(_u32(s ^ y) + _u32(key[(p & 3) ^ e] ^ z ^ 0x7B3A207F))
            v[p] = z = _u32(v[p] + mx)
        rounds -= 1
    return struct.pack(f"<{n}I", *v) + data[n * 4:]


# ---------------------------------------------------------------- ADPCM
_IDX = np.array([-1, -1, -1, -1, 2, 4, 6, 8] * 2, np.int32)
_STEP = np.array([
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767], np.int32)


def decode_adpcm(data: bytes, samples: int) -> np.ndarray:
    """IMA ADPCM da Rockstar: blocos de 2048 bytes (4 de cabeçalho + 2044 de nibbles)."""
    out = np.zeros(samples, np.int16)
    step_t = _STEP.tolist()
    idx_t = _IDX.tolist()
    w = 0
    mv = memoryview(data)
    for bo in range(0, len(data), 2048):
        if w >= samples:
            break
        if bo + 4 > len(data):
            break
        si = min(max(mv[bo], 0), 88)
        pred = struct.unpack_from("<h", data, bo + 2)[0]
        blk = data[bo + 4:bo + 2048]
        res = []
        for byte in blk:
            for nib in (byte & 0x0F, byte >> 4):
                st = step_t[si]
                diff = (((nib & 7) << 1) + 1) * st >> 3
                pred = pred - diff if nib & 8 else pred + diff
                si += idx_t[nib]
                si = 0 if si < 0 else (88 if si > 88 else si)
                res.append(-32768 if pred < -32768 else (32767 if pred > 32767 else pred))
            if w + len(res) >= samples:
                break
        n = min(len(res), samples - w)
        out[w:w + n] = res[:n]
        w += n
    return out


# ---------------------------------------------------------------- estruturas
class Stream:
    def __init__(self, sid):
        self.id = sid
        self.chunks = []   # listas [tipo, bytes]

    def chunk(self, t):
        for c in self.chunks:
            if c[0] == t:
                return c
        return None

    # formato (canal único)
    def fmt(self):
        c = self.chunk(T_FORMAT)
        if not c:
            return None
        b = c[1]
        samples, loop, rate, headroom, lb, le, pe, pb, codec = struct.unpack_from("<IiHhHHHBB", b, 0)
        return dict(samples=samples, rate=rate, codec=codec, headroom=headroom)


class Awc:
    def __init__(self, data: bytes, awc_key):
        self.key = awc_key
        self.whole_enc = False
        magic = struct.unpack_from("<I", data, 0)[0]
        if magic != 0x54414441:
            if len(data) % 4 == 0:
                data = rsxxtea_decrypt(data, awc_key)
                self.whole_enc = True
            magic = struct.unpack_from("<I", data, 0)[0]
            if magic != 0x54414441:
                raise ValueError("AWC inválido")
        _, self.version, self.flags, n, self.data_offset = struct.unpack_from("<IHHii", data, 0)
        p = 16
        self.chunk_indices = None
        if self.flags & 1:
            self.chunk_indices = list(struct.unpack_from(f"<{n}H", data, p))
            p += 2 * n
        infos = struct.unpack_from(f"<{n}I", data, p)
        p += 4 * n
        self.streams = []
        for raw in infos:
            s = Stream(raw & 0x1FFFFFFF)
            for _ in range(raw >> 29):
                rv = struct.unpack_from("<Q", data, p)[0]
                p += 8
                t, size, off = rv >> 56, (rv >> 28) & 0x0FFFFFFF, rv & 0x0FFFFFFF
                s.chunks.append([t, bytes(data[off:off + size]), off, False, size])
            self.streams.append(s)
        self.by_id = {s.id: s for s in self.streams}

    single_enc = property(lambda self: bool(self.flags & 2))
    multi = property(lambda self: bool(self.flags & 4))
    multi_enc = property(lambda self: bool(self.flags & 8))

    # ---- lista de sons "tocáveis"
    def sons(self):
        """[(id, samples, rate, codec, multicanal)]"""
        out = []
        if self.multi:
            src = self._mc_source()
            if src:
                sf = self._streamformat(src)
                for ch in sf["channels"]:
                    out.append((ch["id"], ch["samples"], ch["rate"], ch["codec"], True))
            return out
        for s in self.streams:
            f = s.fmt()
            if f and s.chunk(T_DATA):
                out.append((s.id, f["samples"], f["rate"], f["codec"], False))
        return out

    def _mc_source(self):
        for s in self.streams:
            if s.chunk(T_STREAMFMT) and s.chunk(T_DATA):
                return s
        return None

    @staticmethod
    def _streamformat(s):
        b = s.chunk(T_STREAMFMT)[1]
        bc, bs, cc = struct.unpack_from("<III", b, 0)
        chs = []
        for i in range(cc):
            cid, smp, hr, rate, codec, _, _ = struct.unpack_from("<IIhHBBH", b, 12 + 16 * i)
            chs.append(dict(id=cid, samples=smp, rate=rate, codec=codec))
        return dict(block_count=bc, block_size=bs, channels=chs)

    def _mc_channel_raw(self, ch_index):
        src = self._mc_source()
        sf = self._streamformat(src)
        data = src.chunk(T_DATA)[1]
        cc = len(sf["channels"])
        parts = []
        for b in range(sf["block_count"]):
            blk = data[b * sf["block_size"]:(b + 1) * sf["block_size"]]
            if self.multi_enc and not self.whole_enc:
                blk = rsxxtea_decrypt(blk, self.key)
            hdr = [struct.unpack_from("<6i", blk, 24 * i) for i in range(cc)]
            p = 24 * cc + 4 * sum(h[1] for h in hdr)
            p += (-p) % 0x800
            for i, h in enumerate(hdr):
                n = h[1] * 2048
                if i == ch_index:
                    parts.append(blk[p:p + n])
                p += n
        return b"".join(parts)

    def pcm(self, sid):
        """Retorna (int16 mono, taxa)."""
        if self.multi:
            src = self._mc_source()
            sf = self._streamformat(src)
            for i, ch in enumerate(sf["channels"]):
                if ch["id"] == sid:
                    raw = self._mc_channel_raw(i)
                    if ch["codec"] == CODEC_ADPCM:
                        return decode_adpcm(raw, ch["samples"]), ch["rate"]
                    return np.frombuffer(raw[:ch["samples"] * 2], "<i2").copy(), ch["rate"]
            raise KeyError(sid)
        s = self.by_id[sid]
        f = s.fmt()
        c = s.chunk(T_DATA)
        raw = c[1]
        if self.single_enc and not self.whole_enc and not c[3]:
            raw = rsxxtea_decrypt(raw, self.key)
        if f["codec"] == CODEC_ADPCM:
            return decode_adpcm(raw, f["samples"]), f["rate"]
        if f["codec"] == CODEC_PCM:
            return np.frombuffer(raw[:f["samples"] * 2], "<i2").copy(), f["rate"]
        raise ValueError(f"codec {f['codec']} não suportado")

    # ---- troca
    def trocar(self, sid, pcm16: np.ndarray, rate=None):
        if self.multi:
            raise ValueError("Áudio multicanal (música/cutscene) ainda não pode ser trocado.")
        s = self.by_id[sid]
        f = s.fmt()
        rate = rate or f["rate"]
        pcm16 = np.asarray(pcm16, np.int16)
        if len(pcm16) % 2:
            pcm16 = np.append(pcm16, np.int16(0))
        data = pcm16.astype("<i2").tobytes()
        dc = s.chunk(T_DATA)
        dc[1] = data
        dc[3] = True
        fc = s.chunk(T_FORMAT)
        b = bytearray(fc[1])
        samples = len(pcm16)
        struct.pack_into("<I", b, 0, samples)
        struct.pack_into("<H", b, 8, rate)
        b[19] = CODEC_PCM
        # loop
        lp = struct.unpack_from("<i", b, 4)[0]
        if lp != -1:
            struct.pack_into("<i", b, 4, -1)
        # play end (uint16) zera se não couber
        struct.pack_into("<H", b, 16, 0)
        # picos
        a = np.minimum(np.abs(pcm16.astype(np.int32)) * 2, 65535).astype(np.uint16)
        peak_size = 4096

        def peak(n):
            seg = a[n * peak_size:(n + 1) * peak_size]
            return int(seg.max()) if len(seg) else 0
        if len(b) >= 24:
            struct.pack_into("<H", b, 20, peak(0))
            count = max(0, (samples - peak_size) // peak_size)
            vals = [peak(n + 1) for n in range(count)]
            pc = s.chunk(T_PEAK)
            if count:
                pdata = struct.pack(f"<{count}H", *vals)
                if pc:
                    pc[1] = pdata
                else:
                    s.chunks.append([T_PEAK, pdata, 1 << 30, True, len(pdata)])
            elif pc:
                s.chunks.remove(pc)
        fc[1] = bytes(b)
        # dados de gesto/animação (lábios) ficam como estão

    def salvar(self) -> bytes:
        n = len(self.streams)
        info_start = 16 + (2 * n if self.flags & 1 else 0)
        data_off = info_start + 4 * n + 8 * sum(len(s.chunks) for s in self.streams)
        allc = []
        for si, s in enumerate(self.streams):
            for c in s.chunks:
                allc.append((si, c))
        if self.multi or not self.single_enc:
            allc.sort(key=lambda x: (_sort_order(x[1][0]), x[1][2]))
        else:
            allc.sort(key=lambda x: x[1][2])
        body = bytearray()
        pos = {}
        cur = data_off
        prev_end = self.data_offset
        for si, c in allc:
            orig = c[2]
            if orig < (1 << 30):
                gap = max(0, orig - prev_end)
                cur_gap = gap
                # mantém o mesmo alinhamento (mod 16) do original
                while (cur + cur_gap) % 16 != orig % 16 and cur_gap < gap + 16:
                    cur_gap += 1
                if (cur + cur_gap) % 16 != orig % 16:
                    cur_gap = gap
            else:
                al = _align(c[0])
                cur_gap = (-cur) % al if al else 0
            body += b"\0" * cur_gap
            cur += cur_gap
            d = c[1]
            if c[0] == T_DATA and c[3] and self.single_enc and not self.whole_enc and not self.multi:
                if len(d) % 4:
                    d = d + b"\0" * ((-len(d)) % 4)
                d = rsxxtea_encrypt(d, self.key)
            pos[id(c)] = (cur, len(d))
            body += d
            cur += len(d)
            if orig < (1 << 30):
                prev_end = orig + c[4]
        hdr = bytearray(struct.pack("<IHHii", 0x54414441, self.version, self.flags, n, data_off))
        if self.flags & 1:
            idx, k = [], 0
            for s in self.streams:
                idx.append(k)
                k += len(s.chunks)
            hdr += struct.pack(f"<{n}H", *idx)
        for s in self.streams:
            hdr += struct.pack("<I", (s.id & 0x1FFFFFFF) | (len(s.chunks) << 29))
        for s in self.streams:
            for c in s.chunks:
                off, size = pos[id(c)]
                hdr += struct.pack("<Q", (off & 0x0FFFFFFF) | ((size & 0x0FFFFFFF) << 28) | (c[0] << 56))
        out = bytes(hdr) + bytes(body)
        if self.whole_enc:
            if len(out) % 4:
                out += b"\0" * ((-len(out)) % 4)
            out = rsxxtea_encrypt(out, self.key)
        return out


# ---------------------------------------------------------------- versões rápidas (numba)
if njit is not None:
    @njit(cache=True)
    def _xx_dec(v, key):
        M = 0xFFFFFFFF
        n = v.shape[0]
        i = (0x9E3779B9 * (6 + 52 // n)) & M
        b = v[0]
        while True:
            for bi in range(n - 1, -1, -1):
                a = v[(bi if bi > 0 else n) - 1]
                mx = ((((a >> 5) ^ ((b << 2) & M)) + ((b >> 3) ^ ((a << 4) & M))) & M) ^                      (((i ^ b) + (key[(bi & 3) ^ ((i >> 2) & 3)] ^ a ^ 0x7B3A207F)) & M)
                v[bi] = (v[bi] - mx) & M
                b = v[bi]
            i = (i - 0x9E3779B9) & M
            if i == 0:
                break

    @njit(cache=True)
    def _xx_enc(v, key):
        M = 0xFFFFFFFF
        n = v.shape[0]
        rounds = 6 + 52 // n
        s = 0
        z = v[n - 1]
        while rounds > 0:
            s = (s + 0x9E3779B9) & M
            e = (s >> 2) & 3
            for p in range(n):
                y = v[(p + 1) % n]
                mx = ((((z >> 5) ^ ((y << 2) & M)) + ((y >> 3) ^ ((z << 4) & M))) & M) ^                      (((s ^ y) + (key[(p & 3) ^ e] ^ z ^ 0x7B3A207F)) & M)
                v[p] = (v[p] + mx) & M
                z = v[p]
            rounds -= 1

    @njit(cache=True)
    def _adpcm(data, samples, step_t, idx_t):
        out = np.zeros(samples, np.int16)
        w = 0
        for bo in range(0, data.shape[0], 2048):
            if w >= samples or bo + 4 > data.shape[0]:
                break
            si = min(max(np.int32(data[bo]), 0), 88)
            pred = np.int32(np.int16(np.uint16(data[bo + 2]) | (np.uint16(data[bo + 3]) << 8)))
            end = min(bo + 2048, data.shape[0])
            for j in range(bo + 4, end):
                byte = np.int32(data[j])
                for k in range(2):
                    nib = (byte & 0x0F) if k == 0 else (byte >> 4)
                    st = step_t[si]
                    diff = ((((nib & 7) << 1) + 1) * st) >> 3
                    if nib & 8:
                        pred -= diff
                    else:
                        pred += diff
                    si += idx_t[nib]
                    si = 0 if si < 0 else (88 if si > 88 else si)
                    if w < samples:
                        out[w] = -32768 if pred < -32768 else (32767 if pred > 32767 else pred)
                        w += 1
        return out

    def _wrap_xx(fn):
        def run(data, key):
            n = len(data) // 4
            if n < 2:
                return data
            v = np.frombuffer(data[:n * 4], "<u4").astype(np.int64)
            fn(v, np.array(key, np.int64))
            return v.astype("<u4").tobytes() + data[n * 4:]
        return run

    rsxxtea_decrypt = _wrap_xx(_xx_dec)
    rsxxtea_encrypt = _wrap_xx(_xx_enc)

    def decode_adpcm(data, samples):
        return _adpcm(np.frombuffer(data, np.uint8), int(samples), _STEP.astype(np.int32), _IDX.astype(np.int32))


def indice(ler, key):
    """Lista os sons de um AWC lendo só o necessário.

    ler(off, n) -> bytes (n=None lê tudo). Retorna [(id, samples, rate, codec, multicanal)].
    """
    hdr = ler(0, 16)
    magic, _, flags, n, data_off = struct.unpack_from("<IHHii", hdr, 0)
    if magic != 0x54414441 or (flags & 4):
        return Awc(ler(0, None), key).sons()
    h = ler(0, data_off)
    p = 16 + (2 * n if flags & 1 else 0)
    infos = struct.unpack_from(f"<{n}I", h, p)
    p += 4 * n
    out = []
    for raw in infos:
        fmt = None
        has_data = False
        for _ in range(raw >> 29):
            rv = struct.unpack_from("<Q", h, p)[0]
            p += 8
            t, size, off = rv >> 56, (rv >> 28) & 0x0FFFFFFF, rv & 0x0FFFFFFF
            if t == T_FORMAT:
                fmt = (off, size)
            elif t == T_DATA:
                has_data = True
        if fmt and has_data:
            b = ler(fmt[0], 20)
            samples, _, rate, _, _, _, _, _, codec = struct.unpack_from("<IiHhHHHBB", b, 0)
            out.append((raw & 0x1FFFFFFF, samples, rate, codec, False))
    return out
