"""Dublador GTA V - ouça, grave e substitua as vozes do jogo."""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import sounddevice as sd
import soundfile as sf

import projeto_v as P

REC_SR = 48000
MAX_ROWS = 3000


class App:
    def __init__(self, root):
        self.root = root
        self.p = P.Projeto()
        self.q = queue.Queue()
        self.busy = False
        self.cur = None            # chave da fala selecionada
        self.cur_info = None       # (samples, rate, codec, multi)
        self.rec_stream = None
        self.rec_chunks = []
        self.idx = {}
        self.info = {}             # key -> (samples, rate, codec, multi)
        self.auto = {}
        root.title("Dublador GTA V")
        root.geometry("1200x740")
        self._build_ui()
        root.after(100, self._poll)
        root.after(200, self.load_game)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        st = ttk.Style()
        try:
            st.theme_use("vista")
        except tk.TclError:
            pass
        st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Pasta do jogo:").pack(side="left")
        self.lbl_game = ttk.Label(top, text="", foreground="#555")
        self.lbl_game.pack(side="left", padx=4)
        ttk.Button(top, text="Trocar...", command=self.choose_game).pack(side="left")
        ttk.Button(top, text="Restaurar áudio original", command=self.restore).pack(side="right")
        ttk.Button(top, text="✅ APLICAR NO JOGO", style="Big.TButton", command=self.apply).pack(side="right", padx=6)
        mf = ttk.Frame(top)
        mf.pack(side="right", padx=8)
        ttk.Label(mf, text="Modo:").pack(side="left")
        self.var_modo = tk.StringVar(value=self.p.cfg.get("modo", "auto"))
        cb = ttk.Combobox(mf, textvariable=self.var_modo, width=8, state="readonly", values=["auto", "mods", "direto"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._set_modo())
        self.lbl_modo = ttk.Label(mf, text="", foreground="#555")
        self.lbl_modo.pack(side="left", padx=4)

        pan = ttk.PanedWindow(self.root, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=6)

        left = ttk.Frame(pan)
        pan.add(left, weight=3)
        sb = ttk.Frame(left)
        sb.pack(fill="x", pady=(0, 4))
        ttk.Label(sb, text="Buscar texto/nome/ID:").pack(side="left")
        self.var_search = tk.StringVar()
        e = ttk.Entry(sb, textvariable=self.var_search)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda _e: self.search())
        ttk.Button(sb, text="Buscar", command=self.search).pack(side="left")
        ttk.Button(sb, text="Só as já dubladas", command=lambda: self.search(only_done=True)).pack(side="left", padx=2)
        ttk.Button(sb, text="Lista completa", command=self.fill_tree).pack(side="left")

        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("dur", "txt"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Áudio")
        self.tree.heading("dur", text="Duração")
        self.tree.heading("txt", text="Texto / tradução")
        self.tree.column("#0", width=340)
        self.tree.column("dur", width=70, anchor="e")
        self.tree.column("txt", width=320)
        self.tree.tag_configure("done", foreground="#0a7d16")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<space>", lambda _e: (self.play_orig(), "break")[1])

        right = ttk.Frame(pan, padding=(10, 0))
        pan.add(right, weight=2)
        self.lbl_title = ttk.Label(right, text="Selecione uma fala na lista", font=("Segoe UI", 13, "bold"))
        self.lbl_title.pack(anchor="w")
        self.lbl_info = ttk.Label(right, text="", justify="left", foreground="#444")
        self.lbl_info.pack(anchor="w", pady=(2, 8))

        g1 = ttk.LabelFrame(right, text=" Original ", padding=8)
        g1.pack(fill="x")
        ttk.Button(g1, text="▶ Ouvir (Espaço)", command=self.play_orig).pack(side="left")
        ttk.Button(g1, text="⏹ Parar", command=sd.stop).pack(side="left", padx=4)
        ttk.Button(g1, text="💾 Exportar WAV", command=self.export_one).pack(side="left")

        g2 = ttk.LabelFrame(right, text=" Sua voz ", padding=8)
        g2.pack(fill="x", pady=8)
        r1 = ttk.Frame(g2)
        r1.pack(fill="x")
        self.btn_rec = ttk.Button(r1, text="⏺ Gravar (R)", command=self.toggle_rec)
        self.btn_rec.pack(side="left")
        ttk.Button(r1, text="📂 Importar arquivo...", command=self.import_one).pack(side="left", padx=4)
        r2 = ttk.Frame(g2)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="▶ Ouvir minha versão", command=self.play_mine).pack(side="left")
        ttk.Button(r2, text="🗑 Voltar ao original", command=self.remove_one).pack(side="left", padx=4)
        r3 = ttk.Frame(g2)
        r3.pack(fill="x", pady=(6, 0))
        self.var_trim = tk.BooleanVar(value=self.p.cfg.get("trim", True))
        self.var_vol = tk.BooleanVar(value=self.p.cfg.get("volume", True))
        ttk.Checkbutton(r3, text="Cortar silêncio do início/fim", variable=self.var_trim).pack(anchor="w")
        ttk.Checkbutton(r3, text="Igualar volume ao original", variable=self.var_vol).pack(anchor="w")
        self.lbl_rec = ttk.Label(g2, text="", foreground="#b00")
        self.lbl_rec.pack(anchor="w", pady=(4, 0))

        g3 = ttk.LabelFrame(right, text=" Texto / tradução (para você se organizar) ", padding=8)
        g3.pack(fill="x")
        self.txt = tk.Text(g3, height=4, wrap="word", font=("Segoe UI", 10))
        self.txt.pack(fill="x")
        ttk.Button(g3, text="Salvar texto", command=self.save_note).pack(anchor="e", pady=(4, 0))

        g4 = ttk.LabelFrame(right, text=" Em lote (para usar no Audacity etc.) ", padding=8)
        g4.pack(fill="x", pady=8)
        ttk.Button(g4, text="Exportar arquivo AWC selecionado (todas as falas)...", command=self.export_batch).pack(fill="x")
        ttk.Button(g4, text="Importar pasta com WAVs editados...", command=self.import_batch).pack(fill="x", pady=(4, 0))
        ttk.Button(g4, text="Próxima fala ▶ (N)", command=self.next_item).pack(fill="x", pady=(4, 0))

        bot = ttk.Frame(self.root, padding=6)
        bot.pack(fill="x")
        self.prog = ttk.Progressbar(bot, length=220, maximum=1.0)
        self.prog.pack(side="right")
        self.lbl_status = ttk.Label(bot, text="")
        self.lbl_status.pack(side="left")

        self.root.bind("<KeyPress-r>", lambda e: self._hotkey(e, self.toggle_rec))
        self.root.bind("<KeyPress-n>", lambda e: self._hotkey(e, self.next_item))

    def _hotkey(self, e, fn):
        if isinstance(e.widget, (tk.Text, tk.Entry, ttk.Entry)):
            return
        fn()

    def status(self, msg):
        self.lbl_status.config(text=msg)

    def _set_modo(self):
        self.p.cfg["modo"] = self.var_modo.get()
        self.p.save()
        self._show_modo()

    def _show_modo(self):
        m = self.p.modo()
        txt = {"mods": "→ pasta mods (OpenIV.asi)", "direto": "→ troca os .rpf do jogo (com backup)"}[m]
        if self.p.cfg.get("modo") == "mods" and not self.p.tem_openiv():
            txt += "  ⚠ OpenIV.asi não encontrado"
        self.lbl_modo.config(text=txt)

    # ------------------------------------------------------------------ tarefas em segundo plano
    def _run(self, fn, done=None):
        if self.busy:
            messagebox.showinfo("Dublador", "Espere a tarefa atual terminar.")
            return
        self.busy = True

        def work():
            try:
                res = fn()
                self.q.put(("ok", done, res))
            except Exception as ex:
                import traceback
                traceback.print_exc()
                self.q.put(("err", None, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "prog":
                    self.prog["value"] = a
                    self.status(b)
                elif kind == "ok":
                    self.busy = False
                    self.prog["value"] = 0
                    if a:
                        a(b)
                elif kind == "err":
                    self.busy = False
                    self.prog["value"] = 0
                    self.status("Erro.")
                    messagebox.showerror("Dublador", b)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _progress(self, f, msg):
        self.q.put(("prog", f, msg))

    # ------------------------------------------------------------------ carga
    def load_game(self):
        self.lbl_game.config(text=self.p.game)
        self._show_modo()
        self.tree.delete(*self.tree.get_children())
        if not os.path.exists(os.path.join(self.p.game, "GTA5.exe")):
            messagebox.showerror("Pasta do jogo", f"Não achei o GTA5.exe em:\n{self.p.game}")
            return
        self.status("Lendo a lista de áudios do jogo (a primeira vez demora alguns minutos)...")
        self._run(lambda: self.p.indice(self._progress), self._loaded)

    def _loaded(self, idx):
        self.idx = idx
        self.info = {}
        for rel, awcs in idx.items():
            for awc, sons in awcs:
                for sid, smp, rate, codec, multi in sons:
                    self.info[P.fala_key(rel, awc, sid)] = (smp, rate, codec, multi)
        self.done = self.p.replaced_keys()
        try:
            with open(os.path.join(self.p.dir, "auto", "textos_auto.json"), encoding="utf-8") as f:
                self.auto = json.load(f)
        except Exception:
            self.auto = {}
        self.fill_tree()
        self.status(f"{len(self.info)} áudios em {sum(len(v) for v in idx.values())} arquivos. "
                    f"{len(self.done)} já dublados.")

    def fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for rel, awcs in self.idx.items():
            n = sum(len(s) for _, s in awcs)
            nome = os.path.splitext(os.path.basename(rel))[0]
            if rel.lower().startswith("update"):
                nome = rel.replace("\\", "/").split("/")[-2]
            ico = "🎬" if "CUTSCENE" in rel.upper() else ("🗣" if os.path.basename(rel).upper().startswith(("S", "PAIN")) else "🔊")
            iid = f"pak|{rel}"
            self.tree.insert("", "end", iid, text=f"{ico} {nome} — {P.descricao(rel)}", values=(f"{n}", ""))
            self.tree.insert(iid, "end", f"dummy|{iid}")

    def _row(self, key, parent, iid=None):
        smp, rate, codec, multi = self.info[key]
        done = key in self.done
        rel, awc, sid = P.split_key(key)
        label = f"{sid:08X}" if parent.startswith("awc|") else P.nome_curto(key)
        self.tree.insert(parent, "end", iid or f"snd|{key}", text=label + ("  ✔" if done else ""),
                         values=(f"{smp / max(rate, 1):.1f}s", self.note(key)), tags=("done",) if done else ())

    def _on_open(self, _e):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if not (len(kids) == 1 and kids[0].startswith("dummy|")):
            return
        self.tree.delete(kids[0])
        kind, rest = iid.split("|", 1)
        if kind == "pak":
            for awc, sons in self.idx[rest]:
                sub = f"awc|{rest}#{awc}"
                nd = sum(1 for s in sons if P.fala_key(rest, awc, s[0]) in self.done)
                self.tree.insert(iid, "end", sub, text=f"{os.path.splitext(awc.split('/')[-1])[0]}  ({len(sons)})"
                                 + (f"  ✔{nd}" if nd else ""))
                self.tree.insert(sub, "end", f"dummy|{sub}")
        elif kind == "awc":
            rel, awc = rest.split("#", 1)
            for sons_awc, sons in self.idx[rel]:
                if sons_awc == awc:
                    for s in sons:
                        self._row(P.fala_key(rel, awc, s[0]), iid)
                    break

    def _refresh_row(self, key):
        done = key in self.done
        rel, awc, sid = P.split_key(key)
        for iid, label in ((f"snd|{key}", f"{sid:08X}"), (f"res|{key}", P.nome_curto(key))):
            if self.tree.exists(iid):
                self.tree.item(iid, text=label + ("  ✔" if done else ""), tags=("done",) if done else ())
                self.tree.set(iid, "txt", self.note(key))

    def search(self, only_done=False):
        q = self.var_search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        n = 0
        keys = sorted(self.done) if only_done else self.info.keys()
        for key in keys:
            if key not in self.info:
                continue
            if q and q not in key.lower() and q not in self.note(key).lower():
                continue
            self._row(key, "", f"res|{key}")
            n += 1
            if n >= MAX_ROWS:
                break
        self.status(f"{n} resultados" + (" (mostrando só os primeiros)" if n >= MAX_ROWS else "") + ".")

    # ------------------------------------------------------------------ seleção
    def _on_select(self, _e):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        self.save_note(silent=True)
        if not (iid.startswith("snd|") or iid.startswith("res|")):
            self.cur = None
            return
        key = iid[4:]
        self.cur = key
        smp, rate, codec, multi = self.info[key]
        rel, awc, sid = P.split_key(key)
        self.lbl_title.config(text=P.nome_curto(key))
        extra = "\n⚠ Áudio multicanal (cutscene/música): só dá para ouvir e exportar." if multi else ""
        self.lbl_info.config(text=(
            f"{P.descricao(rel)}\nArquivo: {rel}\n  ↳ {awc}\n"
            f"Duração original: {smp / max(rate, 1):.2f}s   ({rate} Hz, {'ADPCM' if codec == 4 else 'PCM'})\n"
            f"Status: {'DUBLADO ✔' if key in self.done else 'original'}{extra}"))
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", self.note(key))

    def note(self, key):
        return self.p.notes.get(key) or self.auto.get(key, "")

    def _orig(self, key=None):
        x, sr = self.p.pcm_original(key or self.cur)
        return P.to_float(x), sr

    # ------------------------------------------------------------------ ações
    def _need(self):
        if not self.cur:
            messagebox.showinfo("Dublador", "Selecione uma fala na lista primeiro.")
            return False
        return True

    def play_orig(self):
        if self._need():
            try:
                x, sr = self._orig()
            except Exception as ex:
                messagebox.showerror("Dublador", f"Não consegui ler esse áudio:\n{ex}")
                return
            sd.stop()
            sd.play(x, sr)

    def play_mine(self):
        if not self._need():
            return
        p = self.p.sub_path(self.cur)
        if not os.path.exists(p):
            messagebox.showinfo("Dublador", "Essa fala ainda não tem a sua versão.")
            return
        x, sr = sf.read(p, dtype="float32")
        sd.stop()
        sd.play(x, sr)

    def export_one(self):
        if not self._need():
            return
        x, sr = self._orig()
        p = filedialog.asksaveasfilename(defaultextension=".wav",
                                         initialfile=P.nome_curto(self.cur).replace("/", "_") + ".wav",
                                         filetypes=[("WAV", "*.wav")])
        if p:
            sf.write(p, x, sr, subtype="PCM_16")
            self.status(f"Exportado: {p}")

    def _apply_audio(self, x, sr, key=None, quiet=False):
        key = key or self.cur
        if self.info[key][3]:
            raise ValueError("Áudio multicanal (cutscene/música) ainda não pode ser trocado.")
        self.p.cfg["trim"], self.p.cfg["volume"] = self.var_trim.get(), self.var_vol.get()
        orig, orate = self.p.pcm_original(key)
        pcm, rate, msgs = P.preparar(x, sr, orig, orate, trim=self.var_trim.get(), same_volume=self.var_vol.get())
        self.p.save_sub(key, pcm, rate)
        self.done.add(key)
        if threading.current_thread() is threading.main_thread():
            self._refresh_row(key)
        if msgs and not quiet:
            messagebox.showwarning("Dublador", "\n".join(msgs))
        return msgs

    def import_one(self):
        if not self._need():
            return
        p = filedialog.askopenfilename(filetypes=[("Áudio", "*.wav *.mp3 *.ogg *.flac *.aiff"), ("Todos", "*.*")])
        if not p:
            return
        try:
            x, sr = sf.read(p, dtype="float32")
            self._apply_audio(x, sr)
            self.status("Importado. Clique em APLICAR NO JOGO para testar no GTA.")
        except Exception as ex:
            messagebox.showerror("Dublador", f"Não consegui importar:\n{ex}")

    def toggle_rec(self):
        if self.rec_stream:
            self.rec_stream.stop()
            self.rec_stream.close()
            self.rec_stream = None
            self.btn_rec.config(text="⏺ Gravar (R)")
            self.lbl_rec.config(text="")
            if self.rec_chunks:
                x = np.concatenate(self.rec_chunks)[:, 0]
                try:
                    self._apply_audio(x, REC_SR)
                    self.status("Gravação salva. Ouça em 'Ouvir minha versão'.")
                except Exception as ex:
                    messagebox.showerror("Dublador", str(ex))
            return
        if not self._need():
            return
        self.rec_chunks = []
        sd.stop()

        def cb(indata, frames, t, status):
            self.rec_chunks.append(indata.copy())
        try:
            self.rec_stream = sd.InputStream(samplerate=REC_SR, channels=1, dtype="float32", callback=cb)
            self.rec_stream.start()
        except Exception as ex:
            self.rec_stream = None
            messagebox.showerror("Microfone", f"Não consegui abrir o microfone:\n{ex}")
            return
        self.btn_rec.config(text="⏹ Parar gravação (R)")
        self.lbl_rec.config(text="● GRAVANDO... fale agora")

    def remove_one(self):
        if not self._need():
            return
        self.p.remove(self.cur)
        self.done.discard(self.cur)
        self._refresh_row(self.cur)
        self.status("Voltou ao original (aplique no jogo para valer).")

    def save_note(self, silent=False):
        if not self.cur:
            return
        t = self.txt.get("1.0", "end").strip()
        if t == self.note(self.cur):
            return
        if t:
            self.p.notes[self.cur] = t
        else:
            self.p.notes.pop(self.cur, None)
        self.p.save()
        self._refresh_row(self.cur)
        if not silent:
            self.status("Texto salvo.")

    def next_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        nxt = self.tree.next(sel[0])
        if nxt:
            self.tree.selection_set(nxt)
            self.tree.see(nxt)
            self.tree.focus(nxt)

    def export_batch(self):
        sel = self.tree.selection()
        iid = sel[0] if sel else ""
        if iid.startswith(("snd|", "res|")):
            rel, awc, _ = P.split_key(iid[4:])
        elif iid.startswith("awc|"):
            rel, awc = iid[4:].split("#", 1)
        else:
            messagebox.showinfo("Dublador", "Selecione um arquivo (segundo nível da lista) ou uma fala dele.")
            return
        d = filedialog.askdirectory(title="Pasta para salvar os WAVs")
        if not d:
            return

        def work():
            a = self.p.awc(rel, awc)
            sons = a.sons()
            for i, s in enumerate(sons):
                self._progress(i / len(sons), f"Exportando {i + 1}/{len(sons)}")
                x, sr = a.pcm(s[0])
                key = P.fala_key(rel, awc, s[0])
                sf.write(os.path.join(d, key.replace("/", "~") + ".wav"), x, sr, subtype="PCM_16")
            return len(sons)
        self._run(work, lambda n: messagebox.showinfo("Dublador", f"{n} WAVs exportados.\n\nEdite sem mudar o nome "
                                                      "dos arquivos e use 'Importar pasta com WAVs editados'."))

    def import_batch(self):
        d = filedialog.askdirectory(title="Pasta com os WAVs editados")
        if not d:
            return
        files = [f for f in os.listdir(d) if f.lower().endswith(".wav")]

        def work():
            n, warns = 0, []
            for i, f in enumerate(files):
                self._progress(i / max(len(files), 1), f"Importando {f}")
                key = self.p.key_from_file(f)
                if key not in self.info:
                    warns.append(f"{f}: não reconheci o nome")
                    continue
                x, sr = sf.read(os.path.join(d, f), dtype="float32")
                try:
                    w = self._apply_audio(x, sr, key=key, quiet=True)
                    warns += [f"{P.nome_curto(key)}: {m}" for m in w]
                    n += 1
                except Exception as ex:
                    warns.append(f"{f}: {ex}")
            return n, warns
        self._run(work, lambda r: (self.fill_tree(), messagebox.showinfo("Dublador", f"{r[0]} arquivos importados."
                                                      + ("\n\nAvisos:\n" + "\n".join(r[1][:30]) if r[1] else ""))))

    # ------------------------------------------------------------------ jogo
    def choose_game(self):
        d = filedialog.askdirectory(title="Pasta do GTA V (onde fica o GTA5.exe)")
        if d:
            self.p.cfg["game_dir"] = os.path.normpath(d)
            self.p.save()
            self.load_game()

    def apply(self):
        self.save_note(silent=True)
        modo = self.p.modo()
        if not self.p.replaced_keys() and not self.p.aplicados:
            messagebox.showinfo("Dublador", "Você ainda não trocou nenhuma fala.")
            return
        if modo == "direto":
            ok = messagebox.askokcancel("Aplicar no jogo",
                                        "Feche o GTA V antes.\n\nModo DIRETO: os arquivos .rpf do jogo que têm falas "
                                        "trocadas serão regravados. Uma cópia do original vai para a pasta "
                                        "'backup_original' (use 'Restaurar áudio original' para voltar).\n\nContinuar?")
        else:
            ok = messagebox.askokcancel("Aplicar no jogo",
                                        "Feche o GTA V antes.\n\nModo MODS: os arquivos alterados vão para a pasta "
                                        "'mods' do GTA V (o original não é mexido). Precisa do OpenIV.asi instalado.\n\n"
                                        "Continuar?")
        if not ok:
            return
        log = []
        self._run(lambda: self.p.aplicar(log.append, self._progress),
                  lambda r: messagebox.showinfo("Aplicado", f"{r[1]} falas aplicadas (modo {r[0]}).\n\n"
                                                + "\n".join(log[-15:])
                                                + ("\n\nAvisos:\n" + "\n".join(r[2][:20]) if r[2] else "")))

    def restore(self):
        if not messagebox.askokcancel("Restaurar", "Feche o GTA V antes.\n\nVoltar todos os áudios do jogo ao original?\n"
                                      "(Suas gravações continuam guardadas no projeto.)"):
            return
        log = []
        self._run(lambda: self.p.restaurar_tudo(log.append),
                  lambda _r: messagebox.showinfo("Restaurado", "Áudio original de volta.\n\n" + "\n".join(log[-15:])))

    def _close(self):
        self.save_note(silent=True)
        self.p.save()
        if self.rec_stream:
            self.rec_stream.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
