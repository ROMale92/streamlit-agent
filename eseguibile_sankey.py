import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
import io
import re
from datetime import date

st.set_page_config(layout="wide")
st.title("Sankey + Switch (A→B / add-on / drop-off) – linee da regimen giornaliero")

# -----------------------------
# Session state (evita “torna indietro”)
# -----------------------------
for k in ["df_day", "sankey_df", "nodes_df", "switch_events", "switch_summary", "meta"]:
    if k not in st.session_state:
        st.session_state[k] = None

# -----------------------------
# Helpers
# -----------------------------
def _safe_dt(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

def join_unique_sorted(x):
    vals = [str(v).strip() for v in x if pd.notna(v) and str(v).strip() != ""]
    vals = sorted(pd.unique(vals))
    return "+".join(vals)

def collapse_consecutive_regimen(df, id_col, regimen_col, date_col="___DATE___"):
    g = df.sort_values([id_col, date_col]).copy()
    keep = g[regimen_col] != g.groupby(id_col)[regimen_col].shift(1)
    return g[keep].copy()

def compute_lines_by_change(df, id_col, regimen_col):
    d = df.sort_values([id_col, "___DATE___"]).copy()
    d["Linea"] = (
        d.groupby(id_col)[regimen_col]
         .transform(lambda x: x.ne(x.shift()).cumsum())
         .astype(int)
    )
    return d

def _stage_from_label(label: str) -> int:
    m = re.search(r"\(Linea\s+(\d+)\)$", str(label))
    return int(m.group(1)) if m else 10_000

def _pretty_label(s: str, maxlen: int = 28) -> str:
    s = str(s).strip()
    s = s.replace(" + ", "<br>+ ").replace(" (Linea", "<br>(Linea")
    plain = re.sub(r"<br>", " ", s).title()
    if len(plain) > maxlen:
        plain = plain[:maxlen] + "…"
    return plain.replace(" + ", "<br>+ ").replace(" (Linea", "<br>(Linea")

def regimen_to_set(reg):
    if pd.isna(reg) or str(reg).strip() == "":
        return set()
    return set([x.strip() for x in str(reg).split("+") if x.strip()])

def classify_transition(prev_reg, next_reg):
    A = regimen_to_set(prev_reg)
    B = regimen_to_set(next_reg)
    if A == B:
        return "No change"
    if B.issuperset(A) and len(B) > len(A):
        return "Add-on"
    if A.issuperset(B) and len(A) > len(B):
        return "Drop-off"
    return "Switch"

# -----------------------------
# Input
# -----------------------------
file = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"])
if not file:
    st.info("Carica un file per iniziare.")
    st.stop()

df = pd.read_excel(file)
with st.expander("Anteprima"):
    st.dataframe(df.head(), use_container_width=True)

with st.form("setup"):
    c1, c2 = st.columns(2)
    with c1:
        id_col = st.selectbox("Colonna ID paziente", df.columns)
        date_col = st.selectbox("Colonna data erogazione", df.columns)
        therapy_col = st.selectbox("Colonna terapia (ATC / categoria / principio attivo)", df.columns)

    with c2:
        # range date dinamico
        tmp_dates = _safe_dt(df[date_col]).dropna()
        if not tmp_dates.empty:
            MIN_CAL = (tmp_dates.min() - pd.Timedelta(days=3650)).date()
            MAX_CAL = (tmp_dates.max() + pd.Timedelta(days=3650)).date()
            default_naive = tmp_dates.min().date()
            default_fu = tmp_dates.max().date()
        else:
            MIN_CAL = date(1900, 1, 1)
            MAX_CAL = date(2200, 12, 31)
            default_naive = date(2000, 1, 1)
            default_fu = date.today()

        default_naive = max(MIN_CAL, min(MAX_CAL, default_naive))
        default_fu = max(MIN_CAL, min(MAX_CAL, default_fu))

        cutoff_naive = st.date_input(
            "📅 NAÏVE da questa data (prima dispensazione ≥ cutoff)",
            value=default_naive,
            min_value=MIN_CAL,
            max_value=MAX_CAL,
            format="YYYY-MM-DD",
        )
        cutoff_fu = st.date_input(
            "📅 Cut-off follow-up (stato finale)",
            value=default_fu,
            min_value=MIN_CAL,
            max_value=MAX_CAL,
            format="YYYY-MM-DD",
        )

    st.markdown("---")
    c3, c4, c5 = st.columns(3)
    with c3:
        collapse = st.checkbox("Collassa ripetizioni consecutive (regimen uguale)", value=True)
    with c4:
        min_flow = st.number_input("Soglia minima flusso (N)", 1, 999999, 10, 1)
    with c5:
        per_src_min = st.slider("Nascondi link < % della sorgente", 0.0, 20.0, 1.5, 0.5)

    c6, c7, c8 = st.columns(3)
    with c6:
        label_min_total = st.number_input("Mostra etichetta se traffico totale ≥", 0, 999999, 60, 1)
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
            [
                "Inter, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif",
                "Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif",
                "Roboto, Helvetica Neue, Arial, sans-serif",
                "Helvetica Neue, Arial, sans-serif",
                "Calibri, Arial, sans-serif",
            ],
            index=0,
        )

    link_alpha_min = st.slider("Opacità minima link", 0.05, 0.6, 0.15, 0.05)

    submitted = st.form_submit_button("Avvia / Ricalcola")

# -----------------------------
# Compute (solo quando premi)
# -----------------------------
if submitted:
    d = df.copy()
    d[date_col] = _safe_dt(d[date_col])
    d = d.dropna(subset=[date_col])
    d["___DATE___"] = d[date_col]
    d[therapy_col] = d[therapy_col].astype(str).str.strip()

    # NAÏVE: prima dispensazione >= cutoff_naive
    first_disp = d.groupby(id_col)["___DATE___"].min().reset_index()
    naive_ids = first_disp[first_disp["___DATE___"] >= pd.to_datetime(cutoff_naive)][id_col]
    d = d[d[id_col].isin(naive_ids)].copy()

    if d.empty:
        st.session_state.df_day = None
        st.warning("Nessun record dopo il filtro naïve.")
        st.stop()

    # Regimen giornaliero: A+B nello stesso giorno
    df_day = (
        d.groupby([id_col, "___DATE___"], as_index=False)
         .agg(regimen=(therapy_col, join_unique_sorted))
    ).sort_values([id_col, "___DATE___"])

    # Collassa consecutivi uguali
    if collapse:
        df_day = collapse_consecutive_regimen(df_day, id_col=id_col, regimen_col="regimen", date_col="___DATE___")

    # Linea = cambia regimen nel tempo (coerente con l’altro script)
    df_day = compute_lines_by_change(df_day, id_col=id_col, regimen_col="regimen")
    df_day["Terapia"] = df_day["regimen"] + " (Linea " + df_day["Linea"].astype(str) + ")"

    # Esito finale a cutoff_fu
    last_dates = df_day.groupby(id_col)["___DATE___"].max().reset_index()
    last_dates["Esito"] = last_dates["___DATE___"].apply(
        lambda x: "In trattamento" if x >= pd.to_datetime(cutoff_fu) else "Perso al follow-up"
    )
    df_day = df_day.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

    # -------------------------
    # Switch events (tabella)
    # -------------------------
    sw_rows = []
    for pid, g in df_day.sort_values([id_col, "___DATE___"]).groupby(id_col):
        g = g.sort_values("___DATE___")
        prev_reg = None
        prev_line = None
        prev_date = None
        for _, r in g.iterrows():
            if prev_reg is None:
                prev_reg = r["regimen"]
                prev_line = int(r["Linea"])
                prev_date = r["___DATE___"]
                continue
            cur_reg = r["regimen"]
            cur_line = int(r["Linea"])
            cur_date = r["___DATE___"]

            # qui le transizioni avvengono quando cambia regimen (quindi di fatto quando aumenta Linea)
            if cur_reg != prev_reg:
                typ = classify_transition(prev_reg, cur_reg)
                sw_rows.append({
                    "patient_id": str(pid),
                    "from_line": prev_line,
                    "to_line": cur_line,
                    "from_regimen": prev_reg,
                    "to_regimen": cur_reg,
                    "from_date": prev_date,
                    "to_date": cur_date,
                    "switch_type": typ
                })
                prev_reg = cur_reg
                prev_line = cur_line
                prev_date = cur_date

    switch_events = pd.DataFrame(sw_rows)

    if switch_events.empty:
        switch_summary = pd.DataFrame(columns=["switch_type", "N_transitions"])
    else:
        switch_summary = (
            switch_events.groupby("switch_type")
                         .size()
                         .reset_index(name="N_transitions")
                         .sort_values("N_transitions", ascending=False)
        )

    # -------------------------
    # Sankey flows (Linea i -> i+1) + finale->Esito
    # -------------------------
    max_line = int(df_day["Linea"].max())
    flows = []

    for i in range(1, max_line):
        step = df_day[df_day["Linea"].isin([i, i + 1])]
        piv = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
        if not piv.empty:
            f = piv.groupby([i, i + 1]).size().reset_index(name="Count")
            f.columns = ["source", "target", "Count"]
            flows.append(f)

    last_step = df_day.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
    f_end = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
    f_end.columns = ["source", "target", "Count"]
    flows.append(f_end)

    sankey_df = pd.concat(flows, ignore_index=True)

    # filtro assoluto
    sankey_df = sankey_df[sankey_df["Count"] >= int(min_flow)].copy()
    if sankey_df.empty:
        st.session_state.df_day = df_day
        st.session_state.sankey_df = None
        st.warning("Tutti i flussi sono sotto la soglia N impostata.")
        st.stop()

    # filtro per % della sorgente
    tot_src_tmp = sankey_df.groupby("source")["Count"].transform("sum")
    sankey_df["Perc_source_%"] = (sankey_df["Count"] / tot_src_tmp * 100)
    sankey_df = sankey_df[sankey_df["Perc_source_%"] >= float(per_src_min)].copy()
    if sankey_df.empty:
        st.session_state.df_day = df_day
        st.session_state.sankey_df = None
        st.warning("Tutti i flussi sono sotto la soglia percentuale impostata.")
        st.stop()

    # -------------------------
    # Nodes layout
    # -------------------------
    all_labels = pd.unique(sankey_df[["source", "target"]].values.ravel()).tolist()
    stage_map = {lab: _stage_from_label(lab) for lab in all_labels}
    max_line_stage = max([v for v in stage_map.values() if v < 10_000] or [1])

    tot_in = sankey_df.groupby("target")["Count"].sum()
    tot_out = sankey_df.groupby("source")["Count"].sum()
    node_total = (tot_in.add(tot_out, fill_value=0)).to_dict()

    x_pos = {
        lab: (1.0 if stage_map[lab] == 10_000 else (stage_map[lab] - 1) / max(1, max_line_stage - 1))
        for lab in all_labels
    }
