import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import io
import re

st.set_page_config(layout="wide")
st.title("Sankey — Linee terapeutiche (senza aggregazioni)")

# ---------- helpers ----------
def _safe_dt(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

def _collapse_consecutive(df, id_col, cat_col):
    """Rimuove ripetizioni consecutive della stessa categoria per paziente."""
    g = df.sort_values([id_col, "___DATE___"]).copy()
    keep = g[cat_col] != g.groupby(id_col)[cat_col].shift(1)
    return g[keep]

def _assign_lines_by_first_seen(grp, cat_col):
    """Linea = +1 alla prima NUOVA categoria (mai vista prima nel paziente)."""
    seen, out = set(), []
    k = 0
    for v in grp[cat_col]:
        if v not in seen:
            k += 1
            seen.add(v)
        out.append(k)
    return pd.Series(out, index=grp.index)

def _stage_from_label(label: str) -> int:
    """Estrae N da '(Linea N)'; usa 10000 per gli Esiti (così vanno a destra)."""
    m = re.search(r"\(Linea\s+(\d+)\)$", str(label))
    return int(m.group(1)) if m else 10_000

def _pretty_label(s: str, maxlen: int = 28) -> str:
    """Etichette in Title Case, a capo su '+' e prima di '(Linea N)'. """
    s = str(s).strip()
    s = s.replace(" + ", "<br>+ ").replace(" (Linea", "<br>(Linea")
    plain = re.sub(r"<br>", " ", s).title()
    if len(plain) > maxlen:
        plain = plain[:maxlen] + "…"
    return plain.replace(" + ", "<br>+ ").replace(" (Linea", "<br>(Linea")

# ---------- input ----------
file = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"])
if not file:
    st.info("Carica un file per iniziare.")
    st.stop()

df = pd.read_excel(file)
with st.expander("Anteprima"):
    st.dataframe(df.head())

with st.form("setup"):
    c1, c2 = st.columns(2)
    with c1:
        id_col  = st.selectbox("Colonna ID paziente", df.columns)
        cat_col = st.selectbox("Colonna categoria/terapia", df.columns)
    with c2:
        date_col = st.selectbox("Colonna data", df.columns)
        tmp = _safe_dt(df[date_col]).dropna()
        default_naive = (tmp.min().date() if not tmp.empty else pd.Timestamp("2000-01-01").date())
        cutoff_naive = st.date_input("📅 NAÏVE da questa data", value=default_naive)
        cutoff_fu    = st.date_input("📅 Cut-off follow-up (stato finale)", value=pd.Timestamp.today().date())

    c3, c4, c5 = st.columns(3)
    with c3:
        collapse = st.checkbox("Collassa ripetizioni consecutive", value=True)
    with c4:
        min_flow = st.number_input("Soglia minima flusso (N)", 1, 999, 10, 1)
    with c5:
        per_src_min = st.slider("Nascondi link < % della sorgente", 0.0, 20.0, 1.5, 0.5)

    c6, c7, c8 = st.columns(3)
    with c6:
        label_min_total = st.number_input("Mostra etichetta se traffico totale ≥", 0, 999, 60, 1)
    with c7:
        lbl_max = st.number_input("Lunghezza max etichetta", 10, 60, 28, 1)
    with c8:
        fig_height = st.number_input("Altezza grafico (px)", 400, 4000, 1300, 50)

    c9, c10 = st.columns(2)
    with c9:
        font_size = st.slider("Dimensione font", 10, 24, 13, 1)
    with c10:
        font_family = st.selectbox(
            "Font",
            ["Inter, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif",
             "Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif",
             "Roboto, Helvetica Neue, Arial, sans-serif",
             "Helvetica Neue, Arial, sans-serif",
             "Calibri, Arial, sans-serif"],
            index=0
        )

    c11, _ = st.columns(2)
    with c11:
        link_alpha_min = st.slider("Opacità minima link", 0.05, 0.6, 0.15, 0.05)

    submitted = st.form_submit_button("Avvia")

if not submitted:
    st.stop()

# ---------- prep ----------
df = df.copy()
df[date_col] = _safe_dt(df[date_col])
df = df.dropna(subset=[date_col])
df["___DATE___"] = df[date_col]
df[cat_col] = df[cat_col].astype(str).str.strip()

# coorte NAÏVE
first_disp = df.groupby(id_col)["___DATE___"].min().reset_index()
naive_ids = first_disp[first_disp["___DATE___"] >= pd.to_datetime(cutoff_naive)][id_col]
df = df[df[id_col].isin(naive_ids)].sort_values([id_col, "___DATE___"])

if collapse:
    df = _collapse_consecutive(df, id_col, cat_col)
if df.empty:
    st.warning("Nessun record dopo i filtri.")
    st.stop()

# linee terapeutiche = prima comparsa di nuova categoria
df["Linea"] = df.groupby(id_col, group_keys=False).apply(lambda g: _assign_lines_by_first_seen(g, cat_col))
df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"].astype(int).astype(str) + ")"

# esito
last_dates = df.groupby(id_col)["___DATE___"].max().reset_index()
last_dates["Esito"] = last_dates["___DATE___"].apply(
    lambda x: "In trattamento" if x >= pd.to_datetime(cutoff_fu) else "Perso al follow-up"
)
df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

max_line = int(df["Linea"].max())
if max_line < 1:
    st.warning("Dati insufficienti per il Sankey.")
    st.stop()

# ---------- flussi (no aggregazione) ----------
flows = []
# Linea i -> i+1
for i in range(1, max_line):
    step = df[df["Linea"].isin([i, i+1])]
    piv = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
    if not piv.empty:
        f = piv.groupby([i, i+1]).size().reset_index(name="Count")
        f.columns = ["source", "target", "Count"]
        flows.append(f)
# Terapia finale -> Esito
last_step = df.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
f_end = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
f_end.columns = ["source", "target", "Count"]
flows.append(f_end)

sankey_df = pd.concat(flows, ignore_index=True)
# filtro assoluto
sankey_df = sankey_df[sankey_df["Count"] >= int(min_flow)].copy()
if sankey_df.empty:
    st.warning("Tutti i flussi sono sotto la soglia selezionata (N).")
    st.stop()

# filtro per % della sorgente
tot_src_tmp = sankey_df.groupby("source")["Count"].transform("sum")
sankey_df["Perc_source_%"] = (sankey_df["Count"] / tot_src_tmp * 100)
sankey_df = sankey_df[sankey_df["Perc_source_%"] >= float(per_src_min)].copy()
if sankey_df.empty:
    st.warning("Tutti i flussi sono sotto la soglia percentuale impostata.")
    st.stop()

# ---------- layout nodi per fase + ordinamento per traffico ----------
all_labels = pd.unique(sankey_df[["source","target"]].values.ravel()).tolist()
stage_map = {lab: _stage_from_label(lab) for lab in all_labels}
max_line_stage = max([v for v in stage_map.values() if v < 10_000] or [1])

# traffico per nodo (entrate + uscite) per ordering/label
tot_in  = sankey_df.groupby("target")["Count"].sum()
tot_out = sankey_df.groupby("source")["Count"].sum()
node_total = (tot_in.add(tot_out, fill_value=0)).to_dict()

# posizione orizzontale
x_pos = {lab: (1.0 if stage_map[lab]==10_000 else (stage_map[lab]-1)/max(1, max_line_stage-1))
         for lab in all_labels}
# posizione verticale (ordina per traffico decrescente in ogni fase)
y_pos = {}
for stg in sorted(set(stage_map.values())):
    labs = [l for l in all_labels if stage_map[l] == stg]
    labs.sort(key=lambda l: (-node_total.get(l, 0), str(l)))
    n = len(labs)
    ys = [0.5] if n == 1 else np.linspace(0.06, 0.94, n)
    for l, y in zip(labs, ys):
        y_pos[l] = float(y)

# id mapping
id_map = {lab: i for i, lab in enumerate(all_labels)}
sankey_df["source_id"] = sankey_df["source"].map(id_map)
sankey_df["target_id"] = sankey_df["target"].map(id_map)

# percentuali & opacità link (più morbidi)
tot_src = sankey_df.groupby("source")["Count"].transform("sum")
rel = (sankey_df["Count"] / tot_src).fillna(0).clip(0, 1)
gamma = 0.6  # più contrasto tra piccoli/grandi
alphas = (float(link_alpha_min) + (1.0 - float(link_alpha_min)) * (rel ** gamma)).round(3)
link_colors = [f"rgba(120,120,120,{a})" for a in alphas]

# colori nodi (esiti fissi)
palette = px.colors.qualitative.Set3 * 20
node_colors = [palette[i % len(palette)] for i in range(len(all_labels))]
for i, lab in enumerate(all_labels):
    if lab == "In trattamento":
        node_colors[i] = "#8E8CD8"  # lilla
    elif lab == "Perso al follow-up":
        node_colors[i] = "#F2C879"  # sabbia

# etichette: mostra solo sopra soglia totale
labels_pretty = [
    _pretty_label(lab, maxlen=int(lbl_max)) if node_total.get(lab, 0) >= int(label_min_total) else ""
    for lab in all_labels
]

# ---------- plot ----------
fig = go.Figure(go.Sankey(
    arrangement="freeform",
    textfont=dict(family=font_family, size=int(font_size), color="#555"),  # testo più leggero
    node=dict(
        label=labels_pretty,
        pad=34, thickness=24,
        color=node_colors,
        line=dict(color="rgba(0,0,0,0.12)", width=0.3),  # bordo nodo soft
        x=[x_pos[l] for l in all_labels],
        y=[y_pos[l] for l in all_labels],
    ),
    link=dict(
        source=sankey_df["source_id"],
        target=sankey_df["target_id"],
        value=sankey_df["Count"],
        color=link_colors,
        customdata=(sankey_df["Count"] / tot_src * 100).round(1),
        hovertemplate="<b>%{source.label}</b> → <b>%{target.label}</b><br>"
                      "N = %{value}  ( %{customdata}% della sorgente )<extra></extra>",
    )
))
fig.update_layout(
    height=int(fig_height),
    title_text=f"NAÏVE da {pd.to_datetime(cutoff_naive).date()} • FU fino a {pd.to_datetime(cutoff_fu).date()}",
    font=dict(family=font_family, size=int(font_size), color="#444"),
    hoverlabel=dict(font=dict(family=font_family, size=max(int(font_size)-1, 10), color="#444")),
    plot_bgcolor="white", paper_bgcolor="white"
)
st.plotly_chart(fig, use_container_width=True)

# ---------- export ----------
st.subheader("📥 Scarica dati (links + nodes)")
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    sankey_df.to_excel(w, index=False, sheet_name="links")
    pd.DataFrame({
        "id": [id_map[l] for l in all_labels],
        "label": all_labels,
        "label_shown": labels_pretty,
        "stage": [stage_map[l] for l in all_labels],
        "x": [x_pos[l] for l in all_labels],
        "y": [y_pos[l] for l in all_labels],
        "node_total": [int(node_total.get(l, 0)) for l in all_labels],
    }).to_excel(w, index=False, sheet_name="nodes")
st.download_button(
    "💾 Scarica Excel",
    data=buf.getvalue(),
    file_name="sankey_linee.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
