import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import io
import re

st.set_page_config(layout="wide")
st.title("Analisi linee terapeutiche - Sankey (tutte le categorie, niente aggregazione)")

# ---------- helpers ----------
def _safe_dt(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

def _collapse_consecutive(df, id_col, cat_col):
    g = df.sort_values([id_col, "___DATE___"]).copy()
    m = g[cat_col] != g.groupby(id_col)[cat_col].shift(1)
    return g[m]

def _assign_lines_by_first_seen(grp, cat_col):
    seen = set(); out = []; k = 0
    for v in grp[cat_col]:
        if v not in seen:
            k += 1
            seen.add(v)
        out.append(k)
    return pd.Series(out, index=grp.index)

def _stage_from_label(label: str) -> int:
    m = re.search(r"\(Linea\s+(\d+)\)$", str(label))
    return int(m.group(1)) if m else 10_000  # Esiti a destra

def _pretty_label(s: str, maxlen: int = 26) -> str:
    s = str(s).upper().strip()
    s = s.replace(" + ", "<br>+ ")
    s = s.replace(" (LINEA", "<br>(LINEA")
    plain = re.sub(r"<br>", " ", s)
    if len(plain) > maxlen:
        s = (plain[:maxlen] + "…")
    return s

# ---------- input ----------
file = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"])
if not file:
    st.info("Carica un file per iniziare."); st.stop()

df = pd.read_excel(file)
st.success("✅ File caricato!")

with st.expander("Anteprima dati"):
    st.dataframe(df.head())

with st.form("setup"):
    c1, c2 = st.columns(2)
    with c1:
        id_col  = st.selectbox("Colonna identificativo paziente", df.columns)
        cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC / classe)", df.columns)
    with c2:
        date_col = st.selectbox("Colonna data dispensazione", df.columns)
        tmp = _safe_dt(df[date_col]).dropna()
        default_naive = (tmp.min().date() if not tmp.empty else pd.Timestamp("2020-01-01").date())
        cutoff_naive = st.date_input("📅 Seleziona NAÏVE da questa data in poi", value=default_naive)
        cutoff_followup = st.date_input("📅 Cut-off follow-up per stato finale", value=pd.Timestamp.today().date())

    c3, c4, c5 = st.columns(3)
    with c3:
        collapse = st.checkbox("Collassa ripetizioni consecutive", value=True)
    with c4:
        min_flow = st.number_input("Soglia minima flusso (N)", 1, 999, 1, 1)
    with c5:
        lbl_max = st.number_input("Lunghezza max etichetta", 8, 40, 26, 1)

    c6, c7 = st.columns(2)
    with c6:
        font_size = st.slider("Dimensione font", 10, 24, 14, 1)
    with c7:
        link_alpha_min = st.slider("Opacità minima link", 0.05, 0.6, 0.15, 0.05)

    submitted = st.form_submit_button("Avvia analisi")

if not submitted:
    st.stop()

# ---------- prep ----------
df = df.copy()
df[date_col] = _safe_dt(df[date_col])
df = df.dropna(subset=[date_col])
df["___DATE___"] = df[date_col]
df[cat_col] = df[cat_col].astype(str).str.strip().str.upper()

# coorte naïve
first_disp = df.groupby(id_col)["___DATE___"].min().reset_index()
naive_ids = first_disp[first_disp["___DATE___"] >= pd.to_datetime(cutoff_naive)][id_col]
df = df[df[id_col].isin(naive_ids)].sort_values([id_col, "___DATE___"])

if collapse:
    df = _collapse_consecutive(df, id_col, cat_col)
if df.empty:
    st.warning("Nessun record dopo i filtri."); st.stop()

# linee terapeutiche = prima comparsa di nuova categoria
df["Linea"] = df.groupby(id_col, group_keys=False).apply(lambda g: _assign_lines_by_first_seen(g, cat_col))
df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"].astype(int).astype(str) + ")"

# esito
last_dates = df.groupby(id_col)["___DATE___"].max().reset_index()
last_dates["Esito"] = last_dates["___DATE___"].apply(
    lambda x: "In trattamento" if x >= pd.to_datetime(cutoff_followup) else "Perso al follow-up"
)
df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

max_line = int(df["Linea"].max())
if max_line < 1:
    st.warning("Dati insufficienti per il Sankey."); st.stop()

# ---------- flussi ----------
flows = []
for i in range(1, max_line):
    step = df[df["Linea"].isin([i, i+1])]
    piv = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
    if not piv.empty:
        f = piv.groupby([i, i+1]).size().reset_index(name="Count")
        f.columns = ["source", "target", "Count"]
        flows.append(f)

last_step = df.groupby(id_col).agg({"Linea":"max","Terapia":"last","Esito":"last"}).reset_index()
f_end = last_step.groupby(["Terapia","Esito"]).size().reset_index(name="Count")
f_end.columns = ["source","target","Count"]
flows.append(f_end)

sankey_df = pd.concat(flows, ignore_index=True)
sankey_df = sankey_df[sankey_df["Count"] >= int(min_flow)].copy()
if sankey_df.empty:
    st.warning("Tutti i flussi sono sotto la soglia selezionata."); st.stop()

# ---------- layout nodi fisso ----------
all_labels = pd.unique(sankey_df[["source","target"]].values.ravel()).tolist()
stage_map = {lab: _stage_from_label(lab) for lab in all_labels}
max_line_stage = max([v for v in stage_map.values() if v < 10_000] or [1])

x_pos = {lab: (1.0 if stage_map[lab]==10_000 else (stage_map[lab]-1)/max(1, max_line_stage-1)) for lab in all_labels}
y_pos = {}
for stg in sorted(set(stage_map.values())):
    labs = [l for l in all_labels if stage_map[l]==stg]
    n = len(labs)
    ys = [0.5] if n==1 else np.linspace(0.06, 0.94, n)
    for l, y in zip(labs, ys):
        y_pos[l] = float(y)

id_map = {lab:i for i, lab in enumerate(all_labels)}
sankey_df["source_id"] = sankey_df["source"].map(id_map)
sankey_df["target_id"] = sankey_df["target"].map(id_map)

tot_src = sankey_df.groupby("source")["Count"].transform("sum")
sankey_df["Perc_sou_]()_
