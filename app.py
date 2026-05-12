import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import psycopg2
import subprocess
import os
import sys
import json
import glob
from datetime import datetime
import threading

# =============================
# DÉTECTION AUTOMATIQUE
# =============================
def detecter_dossier_app():
    """Détecte le dossier de l'application (là où se trouve app.py)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR     = detecter_dossier_app()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
HIST_FILE   = os.path.join(APP_DIR, "historique.json")
BACKUP_DIR  = os.path.join(APP_DIR, "sauvegardes")

def detecter_pg_path():
    """Détecte automatiquement le chemin de PostgreSQL"""
    chemins_possibles = []

    # Windows
    if os.name == "nt":
        for dossier_base in [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
            "C:\\Program Files",
            "C:\\Program Files (x86)",
        ]:
            pg_dir = os.path.join(dossier_base, "PostgreSQL")
            if os.path.exists(pg_dir):
                versions = sorted(os.listdir(pg_dir), reverse=True)
                for v in versions:
                    bin_path = os.path.join(pg_dir, v, "bin")
                    if os.path.exists(os.path.join(bin_path, "pg_dump.exe")):
                        chemins_possibles.append(bin_path)

        # Chercher aussi dans le PATH
        for p in os.environ.get("PATH", "").split(";"):
            if "postgresql" in p.lower() and os.path.exists(
                os.path.join(p, "pg_dump.exe")
            ):
                chemins_possibles.append(p)

    # Linux / macOS
    else:
        for p in [
            "/usr/bin",
            "/usr/local/bin",
            "/usr/lib/postgresql/*/bin",
            "/opt/homebrew/bin",
        ]:
            resultats = glob.glob(p)
            for r in resultats:
                pg_dump = os.path.join(r, "pg_dump")
                if os.path.exists(pg_dump):
                    chemins_possibles.append(r)

    return chemins_possibles[0] if chemins_possibles else ""

# Couleurs
BG_COLOR      = "#1e1e2e"
CARD_COLOR    = "#2a2a3e"
ACCENT_COLOR  = "#7c3aed"
SUCCESS_COLOR = "#10b981"
ERROR_COLOR   = "#ef4444"
WARN_COLOR    = "#f59e0b"
TEXT_COLOR    = "#ffffff"
SUB_COLOR     = "#a0a0b0"

# =============================
# UTILITAIRES
# =============================
def config_par_defaut():
    return {
        "host":        "localhost",
        "port":        "5432",
        "user":        "postgres",
        "password":    "",
        "base":        "",
        "pg_path":     detecter_pg_path(),
        "backup_dir":  BACKUP_DIR,
        "strategie":   "complete",
        "compression": True,
        "retention":   "7"
    }

def charger_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # Ajouter les clés manquantes
            defaut = config_par_defaut()
            for k, v in defaut.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except:
            return config_par_defaut()
    return config_par_defaut()

def sauvegarder_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def charger_historique():
    if os.path.exists(HIST_FILE):
        try:
            with open(HIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def sauvegarder_historique(hist):
    with open(HIST_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=4, ensure_ascii=False)

def connecter(config, base="postgres"):
    return psycopg2.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["user"],
        password=config["password"],
        database=base
    )

def get_databases(config):
    try:
        conn = connecter(config)
        cur  = conn.cursor()
        cur.execute("""
            SELECT datname FROM pg_database
            WHERE datistemplate = false ORDER BY datname
        """)
        bases = [r[0] for r in cur.fetchall()]
        conn.close()
        return bases, None
    except Exception as e:
        return [], str(e)

def get_metriques(config, base):
    try:
        conn = connecter(config, base)
        cur  = conn.cursor()

        cur.execute("SELECT pg_size_pretty(pg_database_size(%s))", (base,))
        taille_base = cur.fetchone()[0]

        cur.execute("SELECT pg_database_size(%s)", (base,))
        taille_bytes = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        nb_tables = cur.fetchone()[0]

        cur.execute("""
            SELECT
                table_name,
                pg_size_pretty(pg_total_relation_size(quote_ident(table_name))),
                pg_total_relation_size(quote_ident(table_name))
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY pg_total_relation_size(quote_ident(table_name)) DESC
        """)
        tables = cur.fetchall()

        nb_lignes = {}
        for t in tables:
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{t[0]}"')
                nb_lignes[t[0]] = cur.fetchone()[0]
            except:
                nb_lignes[t[0]] = 0

        cur.execute("""
            SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s
        """, (base,))
        connexions = cur.fetchone()[0]

        cur.execute("SELECT version()")
        version = cur.fetchone()[0].split(",")[0]

        cur.execute("""
            SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public'
        """)
        nb_index = cur.fetchone()[0]

        conn.close()
        return {
            "taille_base":  taille_base,
            "taille_bytes": taille_bytes,
            "nb_tables":    nb_tables,
            "tables":       tables,
            "nb_lignes":    nb_lignes,
            "connexions":   connexions,
            "version":      version,
            "nb_index":     nb_index,
        }
    except Exception as e:
        return {"erreur": str(e)}

# =============================
# APPLICATION
# =============================
class CoffrePostgreSQL:
    def __init__(self, root):
        self.root       = root
        self.root.title("Coffre de Sauvegarde PostgreSQL")
        self.root.geometry("1300x800")
        self.root.configure(bg=BG_COLOR)
        self.config     = charger_config()
        self.historique = charger_historique()
        self.connecte   = False
        self.creer_interface()

        # Si pas de mot de passe configuré → aller à la config
        if not self.config.get("password"):
            self.afficher_page("configuration")
            self.root.after(500, lambda: messagebox.showinfo(
                "Bienvenue",
                "Bienvenue dans le Coffre PostgreSQL !\n\n"
                "Commencez par configurer votre connexion\n"
                "à votre serveur PostgreSQL."
            ))
        else:
            self.root.after(500, self.connexion_auto)

    def connexion_auto(self):
        bases, err = get_databases(self.config)
        if bases:
            self.connecte = True
            self.lbl_status.configure(
                text=f"🟢 Connecté | {len(bases)} base(s)",
                fg=SUCCESS_COLOR
            )
        else:
            self.lbl_status.configure(
                text="🔴 Non connecté", fg=ERROR_COLOR
            )

    def creer_interface(self):
        # Header
        header = tk.Frame(self.root, bg=ACCENT_COLOR, height=65)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header,
                 text="COFFRE DE SAUVEGARDE PostgreSQL",
                 font=("Segoe UI", 16, "bold"),
                 bg=ACCENT_COLOR, fg=TEXT_COLOR
                 ).pack(side="left", padx=25, pady=15)

        # Infos système
        pg_path = self.config.get("pg_path", "")
        pg_info = "PostgreSQL detecte" if pg_path else "PostgreSQL non detecte"
        tk.Label(header,
                 text=pg_info,
                 font=("Segoe UI", 9),
                 bg=ACCENT_COLOR, fg="#d4d4ff"
                 ).pack(side="right", padx=25)

        # Body
        body = tk.Frame(self.root, bg=BG_COLOR)
        body.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(body, bg=CARD_COLOR, width=230)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content = tk.Frame(body, bg=BG_COLOR)
        self.content.pack(side="left", fill="both", expand=True)

        self.creer_sidebar()
        self.afficher_page("dashboard")

    def creer_sidebar(self):
        tk.Label(self.sidebar,
                 text="PostgreSQL",
                 font=("Segoe UI", 12, "bold"),
                 bg=CARD_COLOR, fg=ACCENT_COLOR
                 ).pack(pady=(20, 3), padx=15, anchor="w")

        tk.Label(self.sidebar,
                 text=f"{self.config['host']}:{self.config['port']}",
                 font=("Segoe UI", 9),
                 bg=CARD_COLOR, fg=SUB_COLOR
                 ).pack(padx=15, anchor="w")

        tk.Frame(self.sidebar, bg=ACCENT_COLOR, height=2
                 ).pack(fill="x", padx=15, pady=12)

        menus = [
            ("Dashboard",      "dashboard"),
            ("Metriques",      "metriques"),
            ("Configuration",  "configuration"),
            ("Strategie",      "strategie"),
            ("Executer",       "executer"),
            ("Test donnees",   "test_donnees"),
            ("Suivi",          "suivi"),
        ]

        self.btns = {}
        for label, page in menus:
            btn = tk.Button(
                self.sidebar, text=f"  {label}",
                font=("Segoe UI", 11),
                bg=CARD_COLOR, fg=TEXT_COLOR,
                bd=0, padx=20, pady=11,
                anchor="w", cursor="hand2",
                activebackground=ACCENT_COLOR,
                activeforeground=TEXT_COLOR,
                command=lambda p=page: self.afficher_page(p)
            )
            btn.pack(fill="x", pady=1)
            self.btns[page] = btn

        tk.Frame(self.sidebar, bg="#3a3a4e", height=1
                 ).pack(fill="x", padx=15, pady=12)

        self.lbl_status = tk.Label(
            self.sidebar, text="Connexion...",
            font=("Segoe UI", 9),
            bg=CARD_COLOR, fg=SUB_COLOR
        )
        self.lbl_status.pack(padx=15, anchor="w")

        # Infos PostgreSQL détecté
        pg_path = self.config.get("pg_path", "")
        if pg_path:
            version_dir = os.path.basename(os.path.dirname(pg_path))
            tk.Label(self.sidebar,
                     text=f"PG v{version_dir}",
                     font=("Segoe UI", 8),
                     bg=CARD_COLOR, fg=SUB_COLOR
                     ).pack(padx=15, anchor="w", pady=(3, 0))

        tk.Label(self.sidebar,
                 text=f"OS: {os.name}",
                 font=("Segoe UI", 8),
                 bg=CARD_COLOR, fg=SUB_COLOR
                 ).pack(padx=15, anchor="w", pady=(3, 0))

    def afficher_page(self, page):
        for p, b in self.btns.items():
            b.configure(bg=CARD_COLOR)
        self.btns[page].configure(bg=ACCENT_COLOR)
        for w in self.content.winfo_children():
            w.destroy()
        {
            "dashboard":     self.page_dashboard,
            "metriques":     self.page_metriques,
            "configuration": self.page_configuration,
            "strategie":     self.page_strategie,
            "executer":      self.page_executer,
            "test_donnees":  self.page_test_donnees,
            "suivi":         self.page_suivi,
        }[page]()

    # ══════════════════════════════
    # PAGE : DASHBOARD
    # ══════════════════════════════
    def page_dashboard(self):
        self.titre_page("Dashboard")

        total  = len(self.historique)
        succes = len([h for h in self.historique
                      if "Succes" in h.get("statut", "")])

        sf = tk.Frame(self.content, bg=BG_COLOR)
        sf.pack(fill="x", padx=20, pady=10)

        for titre, val, couleur in [
            ("Sauvegardes", str(total),         ACCENT_COLOR),
            ("Succes",      str(succes),         SUCCESS_COLOR),
            ("Echecs",      str(total - succes), ERROR_COLOR),
        ]:
            c = tk.Frame(sf, bg=couleur, width=180, height=85)
            c.pack(side="left", padx=8)
            c.pack_propagate(False)
            tk.Label(c, text=val,
                     font=("Segoe UI", 26, "bold"),
                     bg=couleur, fg=TEXT_COLOR).pack(pady=(10, 0))
            tk.Label(c, text=titre,
                     font=("Segoe UI", 9),
                     bg=couleur, fg=TEXT_COLOR).pack()

        card = self.creer_card(self.content)

        # Vérification PostgreSQL
        pg_path = self.config.get("pg_path", "")
        if not pg_path:
            tk.Label(card,
                     text="PostgreSQL non detecte sur ce systeme !",
                     font=("Segoe UI", 12, "bold"),
                     bg=CARD_COLOR, fg=ERROR_COLOR
                     ).pack(pady=10)
            tk.Label(card,
                     text="Allez dans Configuration pour specifier\n"
                          "le chemin vers PostgreSQL manuellement.",
                     font=("Segoe UI", 10),
                     bg=CARD_COLOR, fg=SUB_COLOR
                     ).pack(pady=5)
            return

        tk.Label(card,
                 text="Bases de donnees disponibles :",
                 font=("Segoe UI", 11, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(0, 10))

        bases, err = get_databases(self.config)
        if bases:
            for base in bases:
                m = get_metriques(self.config, base)
                ligne = tk.Frame(card, bg="#3a3a4e")
                ligne.pack(fill="x", pady=3, ipady=8, padx=5)

                tk.Label(ligne,
                         text=f"  {base}",
                         font=("Segoe UI", 11, "bold"),
                         bg="#3a3a4e", fg=TEXT_COLOR
                         ).pack(side="left", padx=15)

                if "erreur" not in m:
                    tk.Label(ligne,
                             text=f"{m['taille_base']}  |  "
                                  f"{m['nb_tables']} tables  |  "
                                  f"{m['connexions']} conn.",
                             font=("Segoe UI", 9),
                             bg="#3a3a4e", fg=SUB_COLOR
                             ).pack(side="left", padx=10)

                tk.Button(ligne,
                          text="Metriques",
                          font=("Segoe UI", 9),
                          bg="#0891b2", fg=TEXT_COLOR,
                          bd=0, padx=8, pady=4, cursor="hand2",
                          command=lambda b=base: self.voir_metriques(b)
                          ).pack(side="right", padx=5)

                tk.Button(ligne,
                          text="Sauvegarder",
                          font=("Segoe UI", 9),
                          bg=ACCENT_COLOR, fg=TEXT_COLOR,
                          bd=0, padx=8, pady=4, cursor="hand2",
                          command=lambda b=base: self.sauvegarde_rapide(b)
                          ).pack(side="right", padx=5)
        else:
            tk.Label(card,
                     text=f"Impossible de se connecter : {err}",
                     font=("Segoe UI", 11),
                     bg=CARD_COLOR, fg=ERROR_COLOR
                     ).pack(pady=20)
            tk.Label(card,
                     text="Verifiez votre configuration.",
                     font=("Segoe UI", 10),
                     bg=CARD_COLOR, fg=SUB_COLOR
                     ).pack()

    def voir_metriques(self, base):
        self.base_selectionnee = base
        self.afficher_page("metriques")

    def sauvegarde_rapide(self, base):
        self.afficher_page("executer")
        self.root.after(300, lambda: self.combo_bases.set(base)
                        if hasattr(self, "combo_bases") else None)

    # ══════════════════════════════
    # PAGE : MÉTRIQUES
    # ══════════════════════════════
    def page_metriques(self):
        self.titre_page("Metriques de la base de donnees")

        sf = tk.Frame(self.content, bg=BG_COLOR)
        sf.pack(fill="x", padx=20, pady=5)

        tk.Label(sf, text="Base :",
                 font=("Segoe UI", 11, "bold"),
                 bg=BG_COLOR, fg=TEXT_COLOR
                 ).pack(side="left")

        bases, _ = get_databases(self.config)
        self.var_met_base = tk.StringVar(
            value=getattr(self, "base_selectionnee",
                          self.config.get("base", bases[0] if bases else ""))
        )
        combo = ttk.Combobox(sf,
                             textvariable=self.var_met_base,
                             values=bases,
                             font=("Segoe UI", 11),
                             width=25, state="readonly")
        combo.pack(side="left", padx=10, ipady=5)

        tk.Button(sf, text="Actualiser",
                  font=("Segoe UI", 10),
                  bg=ACCENT_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=12, pady=5, cursor="hand2",
                  command=self.page_metriques
                  ).pack(side="left", padx=5)

        base = self.var_met_base.get()
        if not base:
            return

        m = get_metriques(self.config, base)

        if "erreur" in m:
            tk.Label(self.content,
                     text=f"Erreur : {m['erreur']}",
                     font=("Segoe UI", 12),
                     bg=BG_COLOR, fg=ERROR_COLOR
                     ).pack(pady=30)
            return

        cf = tk.Frame(self.content, bg=BG_COLOR)
        cf.pack(fill="x", padx=20, pady=10)

        for titre, val, couleur in [
            ("Taille base",    m["taille_base"],     "#0891b2"),
            ("Tables",         str(m["nb_tables"]),  ACCENT_COLOR),
            ("Connexions",     str(m["connexions"]), SUCCESS_COLOR),
            ("Index",          str(m["nb_index"]),   WARN_COLOR),
        ]:
            c = tk.Frame(cf, bg=couleur, width=180, height=85)
            c.pack(side="left", padx=8)
            c.pack_propagate(False)
            tk.Label(c, text=val,
                     font=("Segoe UI", 20, "bold"),
                     bg=couleur, fg=TEXT_COLOR).pack(pady=(12, 0))
            tk.Label(c, text=titre,
                     font=("Segoe UI", 9),
                     bg=couleur, fg=TEXT_COLOR).pack()

        card = self.creer_card(self.content)

        tk.Label(card,
                 text="Detail des tables :",
                 font=("Segoe UI", 11, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(0, 8))

        header_f = tk.Frame(card, bg=ACCENT_COLOR)
        header_f.pack(fill="x", pady=2)

        for col, w in [("Table", 200), ("Taille", 120),
                       ("Nb lignes", 100), ("Barre", 300)]:
            tk.Label(header_f, text=col,
                     font=("Segoe UI", 10, "bold"),
                     bg=ACCENT_COLOR, fg=TEXT_COLOR,
                     width=w//9
                     ).pack(side="left", padx=5, pady=5)

        taille_max = max(
            (t[2] for t in m["tables"]), default=1
        ) or 1

        for table_name, taille_pretty, taille_bytes in m["tables"]:
            nb    = m["nb_lignes"].get(table_name, 0)
            ratio = taille_bytes / taille_max

            ligne = tk.Frame(card, bg="#3a3a4e")
            ligne.pack(fill="x", pady=2, ipady=6)

            tk.Label(ligne, text=f" {table_name}",
                     font=("Segoe UI", 10, "bold"),
                     bg="#3a3a4e", fg=TEXT_COLOR,
                     width=22, anchor="w"
                     ).pack(side="left", padx=10)

            tk.Label(ligne, text=taille_pretty,
                     font=("Segoe UI", 10),
                     bg="#3a3a4e", fg=WARN_COLOR,
                     width=12
                     ).pack(side="left")

            tk.Label(ligne, text=f"{nb} lignes",
                     font=("Segoe UI", 10),
                     bg="#3a3a4e", fg=SUB_COLOR,
                     width=12
                     ).pack(side="left")

            barre_f = tk.Frame(ligne, bg="#1a1a2e", width=250, height=15)
            barre_f.pack(side="left", padx=10)
            barre_f.pack_propagate(False)

            tk.Frame(barre_f, bg=ACCENT_COLOR,
                     width=int(250 * ratio), height=15
                     ).place(x=0, y=0)

        tk.Label(card,
                 text=f"Version : {m['version']}",
                 font=("Segoe UI", 9),
                 bg=CARD_COLOR, fg=SUB_COLOR
                 ).pack(anchor="w", pady=(15, 0))

    # ══════════════════════════════
    # PAGE : CONFIGURATION
    # ══════════════════════════════
    def page_configuration(self):
        self.titre_page("Configuration de la connexion")

        card = self.creer_card(self.content)

        # ── Connexion PostgreSQL ──
        tk.Label(card,
                 text="Connexion au serveur PostgreSQL :",
                 font=("Segoe UI", 12, "bold"),
                 bg=CARD_COLOR, fg=ACCENT_COLOR
                 ).pack(anchor="w", pady=(0, 10))

        champs = [
            ("Hote (Host)",        "host",     ""),
            ("Port",               "port",     ""),
            ("Utilisateur",        "user",     ""),
            ("Mot de passe",       "password", "*"),
        ]

        self.vars_config = {}
        for label, key, show in champs:
            f = tk.Frame(card, bg=CARD_COLOR)
            f.pack(fill="x", pady=6)
            tk.Label(f, text=label,
                     font=("Segoe UI", 10, "bold"),
                     bg=CARD_COLOR, fg=SUB_COLOR,
                     width=22, anchor="w"
                     ).pack(side="left")
            var = tk.StringVar(value=self.config.get(key, ""))
            tk.Entry(f, textvariable=var,
                     font=("Segoe UI", 11),
                     bg="#3a3a4e", fg=TEXT_COLOR,
                     insertbackground=TEXT_COLOR,
                     bd=0, show=show, width=35
                     ).pack(side="left", ipady=7, padx=5)
            self.vars_config[key] = var

        tk.Frame(card, bg="#3a3a4e", height=1).pack(fill="x", pady=15)

        # ── Chemin PostgreSQL ──
        tk.Label(card,
                 text="Chemin vers PostgreSQL (pg_dump) :",
                 font=("Segoe UI", 12, "bold"),
                 bg=CARD_COLOR, fg=ACCENT_COLOR
                 ).pack(anchor="w", pady=(5, 8))

        pg_frame = tk.Frame(card, bg=CARD_COLOR)
        pg_frame.pack(fill="x", pady=5)

        self.var_pg_path = tk.StringVar(
            value=self.config.get("pg_path", "")
        )
        tk.Entry(pg_frame, textvariable=self.var_pg_path,
                 font=("Segoe UI", 10),
                 bg="#3a3a4e", fg=TEXT_COLOR,
                 insertbackground=TEXT_COLOR,
                 bd=0, width=50
                 ).pack(side="left", ipady=6, padx=5)

        tk.Button(pg_frame, text="Parcourir",
                  font=("Segoe UI", 9),
                  bg=ACCENT_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=10, pady=5, cursor="hand2",
                  command=self.choisir_pg_path
                  ).pack(side="left", padx=5)

        tk.Button(pg_frame, text="Detecter auto",
                  font=("Segoe UI", 9),
                  bg="#0891b2", fg=TEXT_COLOR,
                  bd=0, padx=10, pady=5, cursor="hand2",
                  command=self.detecter_pg_auto
                  ).pack(side="left", padx=5)

        # Status détection
        self.lbl_pg_status = tk.Label(card, text="",
                                       font=("Segoe UI", 9),
                                       bg=CARD_COLOR)
        self.lbl_pg_status.pack(anchor="w", padx=5)

        if self.var_pg_path.get():
            pg_dump_exe = "pg_dump.exe" if os.name == "nt" else "pg_dump"
            if os.path.exists(
                os.path.join(self.var_pg_path.get(), pg_dump_exe)
            ):
                self.lbl_pg_status.configure(
                    text=f"pg_dump trouve dans : {self.var_pg_path.get()}",
                    fg=SUCCESS_COLOR
                )
            else:
                self.lbl_pg_status.configure(
                    text="pg_dump non trouve dans ce dossier !",
                    fg=ERROR_COLOR
                )

        tk.Frame(card, bg="#3a3a4e", height=1).pack(fill="x", pady=15)

        # ── Dossier de sauvegarde ──
        tk.Label(card,
                 text="Dossier de sauvegarde :",
                 font=("Segoe UI", 12, "bold"),
                 bg=CARD_COLOR, fg=ACCENT_COLOR
                 ).pack(anchor="w", pady=(5, 8))

        dir_frame = tk.Frame(card, bg=CARD_COLOR)
        dir_frame.pack(fill="x", pady=5)

        self.var_backup_dir = tk.StringVar(
            value=self.config.get("backup_dir", BACKUP_DIR)
        )
        tk.Entry(dir_frame, textvariable=self.var_backup_dir,
                 font=("Segoe UI", 10),
                 bg="#3a3a4e", fg=TEXT_COLOR,
                 insertbackground=TEXT_COLOR,
                 bd=0, width=50
                 ).pack(side="left", ipady=6, padx=5)

        tk.Button(dir_frame, text="Parcourir",
                  font=("Segoe UI", 9),
                  bg=ACCENT_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=10, pady=5, cursor="hand2",
                  command=lambda: self.var_backup_dir.set(
                      filedialog.askdirectory() or self.var_backup_dir.get()
                  )
                  ).pack(side="left", padx=5)

        # ── Boutons ──
        bf = tk.Frame(card, bg=CARD_COLOR)
        bf.pack(pady=20)

        tk.Button(bf, text="Tester la connexion",
                  font=("Segoe UI", 11, "bold"),
                  bg=ACCENT_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=20, pady=10, cursor="hand2",
                  command=self.tester_connexion
                  ).pack(side="left", padx=10)

        tk.Button(bf, text="Enregistrer",
                  font=("Segoe UI", 11, "bold"),
                  bg=SUCCESS_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=20, pady=10, cursor="hand2",
                  command=self.enregistrer_config
                  ).pack(side="left", padx=10)

        self.lbl_test = tk.Label(card, text="",
                                  font=("Segoe UI", 11),
                                  bg=CARD_COLOR)
        self.lbl_test.pack(pady=10)

    def choisir_pg_path(self):
        dossier = filedialog.askdirectory(
            title="Selectionnez le dossier bin de PostgreSQL"
        )
        if dossier:
            self.var_pg_path.set(dossier)
            pg_dump_exe = "pg_dump.exe" if os.name == "nt" else "pg_dump"
            if os.path.exists(os.path.join(dossier, pg_dump_exe)):
                self.lbl_pg_status.configure(
                    text=f"pg_dump trouve !",
                    fg=SUCCESS_COLOR
                )
            else:
                self.lbl_pg_status.configure(
                    text="pg_dump non trouve dans ce dossier !",
                    fg=ERROR_COLOR
                )

    def detecter_pg_auto(self):
        chemin = detecter_pg_path()
        if chemin:
            self.var_pg_path.set(chemin)
            self.lbl_pg_status.configure(
                text=f"Detecte automatiquement : {chemin}",
                fg=SUCCESS_COLOR
            )
        else:
            self.lbl_pg_status.configure(
                text="PostgreSQL non detecte automatiquement.\n"
                     "Utilisez 'Parcourir' pour le selectionner.",
                fg=ERROR_COLOR
            )

    def tester_connexion(self):
        cfg = {k: v.get() for k, v in self.vars_config.items()}
        bases, err = get_databases(cfg)
        if bases:
            self.lbl_test.configure(
                text=f"Connexion reussie ! Bases : {', '.join(bases)}",
                fg=SUCCESS_COLOR
            )
            self.lbl_status.configure(
                text=f"🟢 Connecte | {len(bases)} base(s)",
                fg=SUCCESS_COLOR
            )
        else:
            self.lbl_test.configure(
                text=f"Echec : {err}", fg=ERROR_COLOR
            )

    def enregistrer_config(self):
        for k, v in self.vars_config.items():
            self.config[k] = v.get()
        self.config["pg_path"]    = self.var_pg_path.get()
        self.config["backup_dir"] = self.var_backup_dir.get()
        sauvegarder_config(self.config)
        self.connexion_auto()
        messagebox.showinfo("Succes", "Configuration enregistree !")

    # ══════════════════════════════
    # PAGE : STRATÉGIE
    # ══════════════════════════════
    def page_strategie(self):
        self.titre_page("Strategie de sauvegarde")
        card = self.creer_card(self.content)

        tk.Label(card, text="Type de sauvegarde :",
                 font=("Segoe UI", 11, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(5, 8))

        self.var_strategie = tk.StringVar(
            value=self.config.get("strategie", "complete")
        )

        for label, val in [
            ("Complete - Structure + donnees",     "complete"),
            ("Schema uniquement - Sans donnees",   "schema"),
            ("Donnees uniquement - Sans structure", "donnees"),
            ("Toutes les bases - pg_dumpall",      "toutes"),
        ]:
            tk.Radiobutton(card, text=label,
                           variable=self.var_strategie, value=val,
                           font=("Segoe UI", 11),
                           bg=CARD_COLOR, fg=TEXT_COLOR,
                           selectcolor=ACCENT_COLOR,
                           activebackground=CARD_COLOR
                           ).pack(anchor="w", pady=6, padx=20)

        tk.Frame(card, bg="#3a3a4e", height=1).pack(fill="x", pady=15)

        rf = tk.Frame(card, bg=CARD_COLOR)
        rf.pack(fill="x", pady=5)
        tk.Label(rf, text="Retention (jours) :",
                 font=("Segoe UI", 11, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(side="left")
        self.var_retention = tk.StringVar(
            value=self.config.get("retention", "7")
        )
        tk.Spinbox(rf, from_=1, to=365,
                   textvariable=self.var_retention,
                   font=("Segoe UI", 11),
                   bg="#3a3a4e", fg=TEXT_COLOR,
                   width=5, bd=0
                   ).pack(side="left", padx=10)

        self.var_compression = tk.BooleanVar(
            value=self.config.get("compression", True)
        )
        tk.Checkbutton(card,
                       text="Activer la compression",
                       variable=self.var_compression,
                       font=("Segoe UI", 11),
                       bg=CARD_COLOR, fg=TEXT_COLOR,
                       selectcolor=ACCENT_COLOR,
                       activebackground=CARD_COLOR
                       ).pack(anchor="w", pady=10, padx=20)

        tk.Button(card, text="Enregistrer la strategie",
                  font=("Segoe UI", 11, "bold"),
                  bg=SUCCESS_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=25, pady=12, cursor="hand2",
                  command=self.enregistrer_strategie
                  ).pack(pady=20)

    def enregistrer_strategie(self):
        self.config["strategie"]   = self.var_strategie.get()
        self.config["retention"]   = self.var_retention.get()
        self.config["compression"] = self.var_compression.get()
        sauvegarder_config(self.config)
        messagebox.showinfo("Succes", "Strategie enregistree !")

    # ══════════════════════════════
    # PAGE : EXÉCUTER
    # ══════════════════════════════
    def page_executer(self):
        self.titre_page("Executer une sauvegarde")
        card = self.creer_card(self.content)

        tk.Label(card, text="Base a sauvegarder :",
                 font=("Segoe UI", 11, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(5, 8))

        bf = tk.Frame(card, bg=CARD_COLOR)
        bf.pack(fill="x", pady=5)

        self.var_base = tk.StringVar()
        self.combo_bases = ttk.Combobox(
            bf, textvariable=self.var_base,
            font=("Segoe UI", 11), width=35, state="readonly"
        )
        self.combo_bases.pack(side="left", padx=5, ipady=6)

        tk.Button(bf, text="Actualiser",
                  font=("Segoe UI", 10),
                  bg=ACCENT_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=10, pady=4, cursor="hand2",
                  command=self.charger_bases
                  ).pack(side="left", padx=5)

        self.charger_bases()

        info = tk.Frame(card, bg="#3a3a4e")
        info.pack(fill="x", pady=10, ipady=6, padx=5)
        tk.Label(info,
                 text=f"Strategie: {self.config.get('strategie','complete').upper()}  "
                      f"| Compression: {'Oui' if self.config.get('compression') else 'Non'}  "
                      f"| Retention: {self.config.get('retention','7')} jours",
                 font=("Segoe UI", 9),
                 bg="#3a3a4e", fg=SUB_COLOR
                 ).pack(padx=10)

        self.progress = ttk.Progressbar(
            card, mode="indeterminate", length=600
        )
        self.progress.pack(pady=10)

        tk.Label(card, text="Log :",
                 font=("Segoe UI", 10, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w")

        self.log = tk.Text(card, height=7,
                           font=("Consolas", 10),
                           bg="#0d0d1a", fg="#00ff88",
                           bd=0, state="disabled")
        self.log.pack(fill="x", pady=8, padx=5)

        self.btn_go = tk.Button(
            card,
            text="LANCER LA SAUVEGARDE",
            font=("Segoe UI", 13, "bold"),
            bg=ACCENT_COLOR, fg=TEXT_COLOR,
            bd=0, padx=35, pady=15, cursor="hand2",
            command=self.lancer_sauvegarde
        )
        self.btn_go.pack(pady=15)

    def charger_bases(self):
        bases, _ = get_databases(self.config)
        if bases:
            self.combo_bases["values"] = bases
            default = self.config.get("base", "")
            if default in bases:
                self.combo_bases.set(default)
            else:
                self.combo_bases.set(bases[0])
        else:
            self.combo_bases["values"] = ["Aucune base trouvee"]

    def log_add(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end",
                        f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def lancer_sauvegarde(self):
        base = self.var_base.get()
        if not base or "Aucune" in base:
            messagebox.showerror("Erreur", "Selectionnez une base valide !")
            return

        pg_path = self.config.get("pg_path", "")
        if not pg_path:
            messagebox.showerror(
                "Erreur",
                "PostgreSQL non configure !\n"
                "Allez dans Configuration pour specifier le chemin."
            )
            return

        self.btn_go.configure(state="disabled", text="En cours...")
        self.progress.start(10)
        self.log_add(f"Demarrage sauvegarde de '{base}'...")
        threading.Thread(
            target=self.executer_sauvegarde,
            args=(base,), daemon=True
        ).start()

    def executer_sauvegarde(self, base):
        try:
            now       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            strategie = self.config.get("strategie", "complete")
            backup_dir = self.config.get("backup_dir", BACKUP_DIR)
            pg_path   = self.config.get("pg_path", "")

            os.makedirs(backup_dir, exist_ok=True)
            fichier = os.path.join(
                backup_dir, f"{base}_{strategie}_{now}.backup"
            )

            pg_dump_exe = "pg_dump.exe" if os.name == "nt" else "pg_dump"
            pg_dump = os.path.join(pg_path, pg_dump_exe)

            cmd = [pg_dump,
                   "-U", self.config["user"],
                   "-h", self.config["host"],
                   "-p", self.config["port"],
                   "-F", "c", "-b", "-f", fichier]

            if strategie == "schema":
                cmd.append("-s")
            elif strategie == "donnees":
                cmd.append("-a")

            if strategie != "toutes":
                cmd.append(base)

            env = os.environ.copy()
            env["PGPASSWORD"] = self.config["password"]

            # Métriques AVANT
            m = get_metriques(self.config, base)
            self.root.after(0, self.log_add, f"Destination : {fichier}")
            self.root.after(0, self.log_add, f"Strategie : {strategie}")
            if "erreur" not in m:
                self.root.after(0, self.log_add,
                                f"Taille base : {m.get('taille_base','?')}  "
                                f"| Tables : {m.get('nb_tables','?')}  "
                                f"| Lignes : {sum(m.get('nb_lignes',{}).values())}")

            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True
            )

            if result.returncode == 0:
                taille_fichier = os.path.getsize(fichier)
                taille_kb = round(taille_fichier / 1024, 2)
                taille_mb = round(taille_fichier / 1024 / 1024, 2)

                entree = {
                    "date":       now,
                    "base":       base,
                    "strategie":  strategie,
                    "fichier":    fichier,
                    "taille":     f"{taille_mb} MB",
                    "nb_tables":  m.get("nb_tables", 0) if "erreur" not in m else 0,
                    "nb_lignes":  sum(m.get("nb_lignes", {}).values()) if "erreur" not in m else 0,
                    "taille_base": m.get("taille_base", "?") if "erreur" not in m else "?",
                    "statut":     "Succes"
                }
                self.historique.insert(0, entree)
                sauvegarder_historique(self.historique)

                self.root.after(0, self.log_add, f"Succes ! Taille : {taille_kb} KB")
                self.root.after(
                    0, messagebox.showinfo,
                    "Sauvegarde reussie",
                    f"Base       : {base}\n"
                    f"Strategie  : {strategie}\n"
                    f"Tables     : {m.get('nb_tables','?') if 'erreur' not in m else '?'}\n"
                    f"Lignes     : {sum(m.get('nb_lignes',{}).values()) if 'erreur' not in m else '?'}\n"
                    f"Taille DB  : {m.get('taille_base','?') if 'erreur' not in m else '?'}\n"
                    f"Fichier    : {taille_kb} KB\n"
                    f"Chemin     : {fichier}"
                )
            else:
                self.root.after(0, self.log_add, f"Erreur : {result.stderr}")
                self.historique.insert(0, {
                    "date": now, "base": base,
                    "strategie": strategie,
                    "fichier": "", "taille": "0 MB",
                    "nb_tables": 0, "nb_lignes": 0,
                    "taille_base": "?",
                    "statut": "Echec"
                })
                sauvegarder_historique(self.historique)
                self.root.after(0, messagebox.showerror,
                                "Erreur", result.stderr)

        except Exception as e:
            self.root.after(0, self.log_add, f"Exception : {e}")
        finally:
            self.root.after(0, self.progress.stop)
            self.root.after(0, self.btn_go.configure,
                            {"state": "normal",
                             "text": "LANCER LA SAUVEGARDE"})

    # ══════════════════════════════
    # PAGE : TEST DONNÉES
    # ══════════════════════════════
    def page_test_donnees(self):
        self.titre_page("Test - Ajouter / Voir des donnees")
        card = self.creer_card(self.content)

        bf = tk.Frame(card, bg=CARD_COLOR)
        bf.pack(fill="x", pady=5)

        tk.Label(bf, text="Base :",
                 font=("Segoe UI", 10, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(side="left")

        bases, _ = get_databases(self.config)
        self.var_test_base = tk.StringVar(
            value=self.config.get("base", bases[0] if bases else "")
        )
        ttk.Combobox(bf,
                     textvariable=self.var_test_base,
                     values=bases,
                     font=("Segoe UI", 10),
                     width=25, state="readonly"
                     ).pack(side="left", padx=10, ipady=5)

        actions = tk.Frame(card, bg=CARD_COLOR)
        actions.pack(fill="x", pady=15)

        boutons = [
            ("+ Client test",    self.ajouter_client_test,  SUCCESS_COLOR),
            ("+ Produit test",   self.ajouter_produit_test, "#0891b2"),
            ("Voir donnees",     self.voir_donnees,         ACCENT_COLOR),
            ("Executer SQL",     self.executer_sql_custom,  WARN_COLOR),
            ("Vider tables",     self.vider_tables,         ERROR_COLOR),
        ]

        for texte, cmd, couleur in boutons:
            tk.Button(actions, text=texte,
                      font=("Segoe UI", 10, "bold"),
                      bg=couleur, fg=TEXT_COLOR,
                      bd=0, padx=12, pady=8,
                      cursor="hand2", command=cmd
                      ).pack(side="left", padx=5)

        # Zone SQL custom
        tk.Label(card, text="Requete SQL :",
                 font=("Segoe UI", 10, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(10, 3))

        self.txt_sql = tk.Text(
            card, height=3,
            font=("Consolas", 10),
            bg="#3a3a4e", fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            bd=0
        )
        self.txt_sql.pack(fill="x", padx=5)
        self.txt_sql.insert("1.0", "SELECT * FROM clients LIMIT 10;")

        tk.Label(card, text="Resultat :",
                 font=("Segoe UI", 10, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(10, 3))

        self.txt_result = tk.Text(
            card, height=12,
            font=("Consolas", 10),
            bg="#0d0d1a", fg="#00ff88",
            bd=0, state="disabled"
        )
        self.txt_result.pack(fill="both", expand=True, pady=5, padx=5)

    def executer_sql_custom(self):
        sql = self.txt_sql.get("1.0", "end").strip()
        if not sql:
            return
        try:
            base = self.var_test_base.get()
            conn = connecter(self.config, base)
            cur  = conn.cursor()
            cur.execute(sql)

            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                result = "  ".join(f"{c:<20}" for c in cols) + "\n"
                result += "-" * (20 * len(cols)) + "\n"
                for r in rows:
                    result += "  ".join(f"{str(v):<20}" for v in r) + "\n"
                result += f"\n{len(rows)} ligne(s)"
            else:
                conn.commit()
                result = f"Requete executee avec succes !\n{cur.rowcount} ligne(s) affectee(s)"

            conn.close()
            self.afficher_result(result)
        except Exception as e:
            self.afficher_result(f"Erreur : {e}")

    def ajouter_client_test(self):
        try:
            base = self.var_test_base.get()
            conn = connecter(self.config, base)
            cur  = conn.cursor()
            now  = datetime.now().strftime("%H%M%S")
            cur.execute("""
                INSERT INTO clients (nom, email, ville, age)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (f"Test User {now}",
                  f"test{now}@test.com",
                  "Paris", 25))
            new_id = cur.fetchone()[0]
            conn.commit()
            conn.close()
            self.afficher_result(
                f"Client ajoute ! ID = {new_id}\n"
                f"Nom   : Test User {now}\n"
                f"Email : test{now}@test.com"
            )
        except Exception as e:
            self.afficher_result(f"Erreur : {e}")

    def ajouter_produit_test(self):
        try:
            base = self.var_test_base.get()
            conn = connecter(self.config, base)
            cur  = conn.cursor()
            now  = datetime.now().strftime("%H%M%S")
            cur.execute("""
                INSERT INTO produits (nom, prix, stock, categorie)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (f"Produit Test {now}", 99.99, 10, "Test"))
            new_id = cur.fetchone()[0]
            conn.commit()
            conn.close()
            self.afficher_result(
                f"Produit ajoute ! ID = {new_id}\n"
                f"Nom  : Produit Test {now}\n"
                f"Prix : 99.99"
            )
        except Exception as e:
            self.afficher_result(f"Erreur : {e}")

    def voir_donnees(self):
        try:
            base = self.var_test_base.get()
            conn = connecter(self.config, base)
            cur  = conn.cursor()

            # Liste des tables
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' ORDER BY table_name
            """)
            tables = [r[0] for r in cur.fetchall()]

            result = f"Base : {base}\nTables trouvees : {len(tables)}\n\n"

            for table in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                count = cur.fetchone()[0]

                cur.execute(f'SELECT * FROM "{table}" LIMIT 5')
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]

                result += f"--- {table} ({count} lignes) ---\n"
                result += "  ".join(f"{c:<18}" for c in cols) + "\n"
                for r in rows:
                    result += "  ".join(f"{str(v):<18}" for v in r) + "\n"
                result += "\n"

            conn.close()
            self.afficher_result(result)
        except Exception as e:
            self.afficher_result(f"Erreur : {e}")

    def vider_tables(self):
        if not messagebox.askyesno(
            "Confirmation",
            "Vider toutes les tables ? Action irreversible !"
        ):
            return
        try:
            base = self.var_test_base.get()
            conn = connecter(self.config, base)
            cur  = conn.cursor()

            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
            """)
            tables = [r[0] for r in cur.fetchall()]

            for t in tables:
                cur.execute(f'TRUNCATE "{t}" RESTART IDENTITY CASCADE')

            conn.commit()
            conn.close()
            self.afficher_result(
                f"Tables videes : {', '.join(tables)}"
            )
        except Exception as e:
            self.afficher_result(f"Erreur : {e}")

    def afficher_result(self, texte):
        self.txt_result.configure(state="normal")
        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("end", texte)
        self.txt_result.configure(state="disabled")

    # ══════════════════════════════
    # PAGE : SUIVI
    # ══════════════════════════════
    def page_suivi(self):
        self.titre_page("Suivi des travaux de sauvegarde")
        card = self.creer_card(self.content)

        total  = len(self.historique)
        succes = len([h for h in self.historique
                      if "Succes" in h.get("statut", "")])

        sf = tk.Frame(card, bg=CARD_COLOR)
        sf.pack(fill="x", pady=10)

        for titre, val, couleur in [
            ("Total",  str(total),         ACCENT_COLOR),
            ("Succes", str(succes),         SUCCESS_COLOR),
            ("Echecs", str(total - succes), ERROR_COLOR),
        ]:
            f = tk.Frame(sf, bg=couleur, width=160, height=75)
            f.pack(side="left", padx=8)
            f.pack_propagate(False)
            tk.Label(f, text=val,
                     font=("Segoe UI", 22, "bold"),
                     bg=couleur, fg=TEXT_COLOR).pack(pady=(10, 0))
            tk.Label(f, text=titre,
                     font=("Segoe UI", 9),
                     bg=couleur, fg=TEXT_COLOR).pack()

        tk.Label(card, text="Historique :",
                 font=("Segoe UI", 11, "bold"),
                 bg=CARD_COLOR, fg=TEXT_COLOR
                 ).pack(anchor="w", pady=(15, 5))

        cols = ("Date", "Base", "Strategie",
                "Tables", "Lignes", "Taille DB",
                "Fichier", "Statut")
        tree = ttk.Treeview(card, columns=cols,
                             show="headings", height=10)

        for col, w in zip(cols, [150, 110, 90, 60, 60, 90, 80, 80]):
            tree.heading(col, text=col)
            tree.column(col, width=w)

        tree.tag_configure("s", background="#1a3a2a", foreground="#10b981")
        tree.tag_configure("e", background="#3a1a1a", foreground="#ef4444")

        for h in self.historique:
            tag = "s" if "Succes" in h.get("statut", "") else "e"
            tree.insert("", "end", values=(
                h.get("date", ""),
                h.get("base", ""),
                h.get("strategie", ""),
                h.get("nb_tables", ""),
                h.get("nb_lignes", ""),
                h.get("taille_base", ""),
                h.get("taille", ""),
                h.get("statut", ""),
            ), tags=(tag,))

        sb = ttk.Scrollbar(card, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        tk.Button(self.content, text="Actualiser",
                  font=("Segoe UI", 10),
                  bg=ACCENT_COLOR, fg=TEXT_COLOR,
                  bd=0, padx=15, pady=8, cursor="hand2",
                  command=lambda: self.afficher_page("suivi")
                  ).pack(pady=10)

    # ══════════════════════════════
    # UTILITAIRES UI
    # ══════════════════════════════
    def titre_page(self, titre):
        f = tk.Frame(self.content, bg=BG_COLOR)
        f.pack(fill="x", padx=20, pady=(20, 5))
        tk.Label(f, text=titre,
                 font=("Segoe UI", 16, "bold"),
                 bg=BG_COLOR, fg=TEXT_COLOR).pack(anchor="w")
        tk.Frame(f, bg=ACCENT_COLOR, height=3).pack(fill="x", pady=5)

    def creer_card(self, parent):
        outer = tk.Frame(parent, bg=CARD_COLOR)
        outer.pack(fill="both", expand=True, padx=20, pady=10)
        inner = tk.Frame(outer, bg=CARD_COLOR)
        inner.pack(fill="both", expand=True, padx=20, pady=20)
        return inner


# =============================
# LANCEMENT
# =============================
if __name__ == "__main__":
    root = tk.Tk()
    app  = CoffrePostgreSQL(root)
    root.mainloop()