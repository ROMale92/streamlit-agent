import streamlit as st
import pandas as pd
import io
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Aderenza terapeutica PDC su persistenza reale – v11 (DDD già nel file)")

# -------------------------------
# Session state init
# -------------------------------
for k in ["df_base", "meta", "aderenza", "riepilogo"]:
    if k not in st.session_state:
        st.session_state[k] = None

# -------------------------------
# Utils
# -------------------------------
@st.cache_data(show_spinner=False)
def _read_excel(file):
    return pd.read_excel(file)

def _safe_numeric(series):
    s = pd.to_numeric(series, errors="coerce")
    return s.where(pd.notnull(s), 0)

def calcola_pdc_persistenza(ev, start, periodo):
    """
    ev: DataFrame con colonne [__date, 'giorni_coperti'] già ordinate per data.
    start: inizio osservazione (Timestamp)
    periodo: giorni (int) della finestra massima

    PDC su persistenza reale = numeratore copertura / denominatore persistenza reale
    persistenza reale = da start a ultimo giorno coperto (troncata alla finestra).
    """
    end = start + pd.Timedelta(days=int(periodo))
    ev = ev[ev["__date"] < end].copy()

    # evento fittizio a "end" per chiudere ultimo intervallo
    ev = pd.concat(
        [ev, pd.DataFrame([{"__date": end, "giorni_coperti": 0.0}])],
        ignore_index=True,
        axis=0,
    ).sort_values("__date")

    prev_date = start
    stock = 0.0
    covered_total = 0.0
    last_covered = None

    for _, row in ev.iterrows():
        date = row["__date"]
        interval_len = (date - prev_date).days
        if interval_len > 0:
            used = min(stock, interval_len)
            covered_total += used
            if used > 0:
                last_covered = prev_date + pd.Timedelta(days=int(used))
            stock -= used
        stock += float(row["giorni_coperti"])
        prev_date = date

    if last_covered is None:
        giorni_persistenza = 0
        pdc_persistenza = 0.0
    else:
        giorni_persistenza = max((min(last_covered, end) - start).days, 0)
        pdc_persistenza = covered_total / giorni_persistenza if giorni_persistenza > 0 else 0.0

    return float(min(max(pdc_persistenza, 0.0), 1.0)), int(giorni_persistenza)


# -------------------------------
# Upload
# -------------------------------
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni (DDD già presente)", type=["xlsx"], key="disp")

if not file_disp:
    st.info("Carica il file per continuare.")
    st.stop()

df = _read_excel(file_disp)
st.success("✅ File caricato!")
st.caption(f"Dispensazioni: {df.shape[0]:,} righe")
st.dataframe(df.head(), use_container_width=True)

# -------------------------------
# Form
# -------------------------------
with st.form("setup"):
    col1, col2, col3 = st.columns(3)

    with col1:
        id_col = st.selectbox("Colonna identificativo paziente", df.columns, key="id_col")
        atc_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns, key="atc_col")
        date_col = st.selectbox("Colonna data dispensazione", df.columns, key="date_col")

    with col2:
        ddd_col = st.selectbox(
            "Colonna DDD consegnate (già normalizzate)",
            df.columns,
            key="ddd_col"
        )
        # opzionale: se nel file c'è Linea, la permettiamo
        line_col = st.selectbox(
            "Colonna Linea (opzionale)",
            options=["(nessuna)"] + list(df.columns),
            index=0,
            key="line_col"
        )

    with col3:
        cutoff_naive = st.date_input("📅 Data indice (per selezionare naïve)", key="cutoff_naive")
        periodo = st.number_input(
            "Finestra massima (giorni)",
            min_value=30, max_value=1825, value=365, step=30, key="periodo"
        )
        soglia = st.number_input(
            "Soglia aderenza (PDC su persistenza)",
            min_value=0.0, max_value=1.0, value=0.80, step=0.05, format="%.2f", key="soglia"
        )

    st.markdown("---")
    col4, col5 = st.columns(2)
    with col4:
        naive_scope = st.radio("Selezione naïve", ["Per paziente", "Per paziente+ATC"], horizontal=True, key="naive_scope")
    with col5:
        unit_scope = st.radio("Unità di analisi", ["Per paziente (ATC principale)", "Per paziente+ATC"], horizontal=True, key="unit_scope")

    submitted = st.form_submit_button("Avvia analisi (PDC su persistenza)")

# -------------------------------
# STEP 1: Prepara e salva dataset (solo quando premi)
# -------------------------------
if submitted:
    d = df.copy()

    # parse date
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce", dayfirst=True)
    d = d.dropna(subset=[date_col])

    # DDD consegnate (già normalizzate) -> giorni coperti
    d["giorni_coperti"] = _safe_numeric(d[ddd_col]).replace([float("inf"), -float("inf")], 0.0)
    d["__date"] = d[date_col]

    # opzionale filtro linea
    linea_target = None
    if line_col != "(nessuna)":
        # per non far crashare se ci sono NaN
        linee = sorted(pd.Series(d[line_col].dropna().unique()).tolist())
        # se non ci sono valori validi, saltiamo
        if len(linee) > 0:
            linea_target = st.session_state.get("linea_target_value", linee[0])
        # salviamo meta e filtreremo più sotto, dopo naïve (così è coerente)
    else:
        linee = []

    # Selezione naïve
    cutoff = pd.to_datetime(cutoff_naive)
    if naive_scope == "Per paziente":
        first_disp = d.groupby(id_col)[date_col].min().reset_index().rename(columns={date_col: "__first_date"})
        naive_ids = first_disp[first_disp["__first_date"] >= cutoff][id_col]
        d = d[d[id_col].isin(naive_ids)].merge(first_disp, on=id_col, how="left")
    else:
        first_disp = d.groupby([id_col, atc_col])[date_col].min().reset_index().rename(columns={date_col: "__first_date"})
        d = d.merge(first_disp, on=[id_col, atc_col], how="left")
        d = d[d["__first_date"] >= cutoff]

    if d.empty:
        st.session_state.df_base = None
        st.error("Nessun paziente/ATC naïve secondo i criteri selezionati.")
    else:
        # Salvo base + meta
        st.session_state.df_base = d
        st.session_state.meta = dict(
            id_col=id_col, atc_col=atc_col, date_col=date_col, ddd_col=ddd_col,
            line_col=line_col, naive_scope=naive_scope, unit_scope=unit_scope,
            cutoff=str(cutoff_naive), periodo=int(periodo), soglia=float(soglia)
        )
        st.success("✅ Dataset preparato e salvato. Ora puoi cambiare impostazioni senza perdere i dati (finché non ripremi Avvia).")

# -------------------------------
# STEP 2: Se ho un dataset pronto, posso calcolare / ricalcolare
# -------------------------------
if st.session_state.df_base is None:
    st.stop()

base = st.session_state.df_base.copy()
meta = st.session_state.meta

# recupero parametri (così restano coerenti anche ai rerun)
id_col = meta["id_col"]
atc_col = meta["atc_col"]
date_col = meta["date_col"]
line_col = meta["line_col"]

# controlli runtime: periodo/soglia possono cambiare anche senza ricliccare “Avvia”
periodo = int(st.session_state.get("periodo", meta["periodo"]))
soglia = float(st.session_state.get("soglia", meta["soglia"]))
unit_scope = st.session_state.get("unit_scope", meta["unit_scope"])

# Se è presente la colonna Linea, scegli la linea in un widget (senza rifare tutta la prep)
linea_target = None
if line_col != "(nessuna)":
    linee_disponibili = sorted(pd.Series(base[line_col].dropna().unique()).tolist())
    if len(linee_disponibili) > 0:
        default_idx = 0
        linea_target = st.selectbox(
            "🎯 Seleziona la Linea su cui calcolare l’aderenza (opzionale)",
            options=linee_disponibili,
            index=default_idx,
            key="linea_target_value"
        )
        base = base[base[line_col] == linea_target].copy()
        st.info(f"Calcolo su Linea = {linea_target} • righe: {len(base):,} • pazienti: {base[id_col].nunique():,}")

if base.empty:
    st.error("Dopo il filtro (es. Linea) non ci sono dati.")
    st.stop()

# -------------------------------
# Calcolo PDC su persistenza
# -------------------------------
risultati = []

# per scegliere “ATC principale” in modo sensato, lo scelgo tra quelli MISURABILI (giorni_coperti > 0)
if unit_scope == "Per paziente (ATC principale)":
    for pid, s in base.sort_values("__date").groupby(id_col):
        s = s.sort_values("__date")

        start = s["__date"].min()
        pdc_pers, giorni_pers = calcola_pdc_persistenza(s[["__date", "giorni_coperti"]], start, periodo)

        # ATC principale: mode tra record con giorni_coperti>0 (se esistono), altrimenti mode su tutto
        s_pos = s[s["giorni_coperti"] > 0]
        if not s_pos.empty and not s_pos[atc_col].mode().empty:
            atc_principale = s_pos[atc_col].mode().iloc[0]
        else:
            atc_principale = s[atc_col].mode().iloc[0] if not s[atc_col].mode().empty else None

        risultati.append({
            id_col: pid,
            "ATC_unit": atc_principale,
            "PDC_persistenza": pdc_pers,
            "Persistenza_giorni": int(giorni_pers),
        })

else:
    for (pid, atc), s in base.sort_values("__date").groupby([id_col, atc_col]):
        s = s.sort_values("__date")
        start = s["__date"].min()
        pdc_pers, giorni_pers = calcola_pdc_persistenza(s[["__date", "giorni_coperti"]], start, periodo)
        risultati.append({
            id_col: pid,
            "ATC_unit": atc,
            "PDC_persistenza": pdc_pers,
            "Persistenza_giorni": int(giorni_pers),
        })

aderenza = pd.DataFrame(risultati)

# -------------------------------
# ESCLUDI PDC=0 (non misurabili) - come deciso
# -------------------------------
n_before = len(aderenza)
aderenza = aderenza[(aderenza["PDC_persistenza"] > 0) & (aderenza["Persistenza_giorni"] > 0)].copy()
n_after = len(aderenza)
st.info(f"Esclusi {n_before - n_after:,} record con PDC_persistenza = 0 o Persistenza_giorni = 0 (rimasti {n_after:,}).")

# Flag aderente
aderenza["Aderente"] = aderenza["PDC_persistenza"] >= soglia

# salva in session_state (utile se vuoi costruire Tabella 1 altrove)
st.session_state.aderenza = aderenza

# -------------------------------
# Riepiloghi
# -------------------------------
st.subheader("📊 PDC su persistenza – risultati unità di analisi (filtrati)")
st.dataframe(aderenza, use_container_width=True)

st.subheader("📊 Riepilogo per ATC_unit (PDC su persistenza)")
riepilogo = aderenza.groupby("ATC_unit").agg(
    N_unit=("PDC_persistenza", "count"),
    N_aderenti=("Aderente", "sum"),
    PDC_medio=("PDC_persistenza", "mean"),
    PDC_std=("PDC_persistenza", "std"),
    P50=("PDC_persistenza", "median"),
    P10=("PDC_persistenza", lambda s: s.quantile(0.10)),
    P90=("PDC_persistenza", lambda s: s.quantile(0.90)),
    PDC_min=("PDC_persistenza", "min"),
    PDC_max=("PDC_persistenza", "max"),
).reset_index()
riepilogo["%_aderenti"] = (100 * riepilogo["N_aderenti"] / riepilogo["N_unit"]).round(1)

st.dataframe(riepilogo, use_container_width=True)
st.session_state.riepilogo = riepilogo

# -------------------------------
# Grafici
# -------------------------------
st.subheader("📈 Distribuzione PDC su persistenza per ATC_unit")
if not aderenza.empty:
    order = aderenza.groupby("ATC_unit")["PDC_persistenza"].median().sort_values().index
    fig = px.box(
        aderenza,
        x=pd.Categorical(aderenza["ATC_unit"], categories=order, ordered=True),
        y="PDC_persistenza",
        points="all",
        title=f"Distribuzione PDC (su persistenza) per ATC_unit – soglia aderente = {soglia:.2f}",
        labels={"x": "ATC_unit", "PDC_persistenza": "PDC su persistenza"}
    )
    st.plotly_chart(fig, use_container_width=True)

# -------------------------------
# Download
# -------------------------------
st.subheader("📥 Scarica risultati")
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    aderenza.to_excel(writer, index=False, sheet_name="PDC_persistenza_unita")
    riepilogo.to_excel(writer, index=False, sheet_name="Riepilogo_ATC_unit")

# nome file con linea se presente
suffix_linea = ""
if line_col != "(nessuna)" and linea_target is not None:
    suffix_linea = f"_linea{linea_target}"

st.download_button(
    label="💾 Scarica risultati (Excel)",
    data=buffer.getvalue(),
    file_name=f"risultati_aderenza_persistenza_v11{suffix_linea}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
