
import streamlit as st
import pandas as pd
import io
import plotly.express as px

st.set_page_config(layout="wide")
st.title("Analisi aderenza terapeutica (PDC) – v11: UI v9, calcolo su persistenza (v10)") 

st.caption("Nota: la colonna/metriche 'PDC' in questa versione sono **calcolate su persistenza reale** (denominatore = durata dalla prima dispensazione all'ultimo giorno coperto, troncato alla finestra).")

# -------------------------------
# Utils
# -------------------------------
@st.cache_data(show_spinner=False)
def _read_excel(file):
    return pd.read_excel(file)

def _safe_numeric(series):
    s = pd.to_numeric(series, errors="coerce")
    s = s.where(pd.notnull(s), 0)
    return s

def _check_unique(tab_ddd, atc_ddd_col):
    dup = tab_ddd[atc_ddd_col].value_counts()
    problematic = dup[dup > 1]
    if not problematic.empty:
        st.warning(
            "⚠️ Nella tabella DDD ci sono ATC duplicati: "
            + ", ".join(map(str, problematic.index[:10]))
            + (" ..." if len(problematic) > 10 else "")
            + ". Questo può duplicare le righe in merge."
        )

def calcola_pdc_persistenza(ev, start, periodo):
    """
    ev: DataFrame con colonne [__date, 'giorni_coperti'] ordinate per data.
    start: inizio osservazione (Timestamp)
    periodo: giorni (int) della finestra massima
    Calcola PDC con denominatore = durata della PERSISTENZA reale
    (dalla prima dispensazione fino all'ultimo giorno coperto), troncata alla finestra.
    Ritorna (pdc_persistenza, giorni_persistenza).
    """
    end = start + pd.Timedelta(days=int(periodo))
    ev = ev[ev["__date"] < end].copy()

    # Evento fittizio per chiudere l'ultimo intervallo
    ev = pd.concat([
        ev,
        pd.DataFrame([{"__date": end, "giorni_coperti": 0.0}])
    ], ignore_index=True, axis=0).sort_values("__date")

    prev_date = start
    stock = 0.0
    covered_total = 0.0  # numeratore (giorni coperti totali)
    last_covered = None  # ultimo istante coperto

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
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"], key="disp")
file_ddd = st.file_uploader("📁 Carica file Excel con tabella DDD (ATC, DDD_standard)", type=["xlsx"], key="ddd")

if file_disp and file_ddd:
    df = _read_excel(file_disp)
    tab_ddd = _read_excel(file_ddd)
    st.success("✅ File caricati!")
    st.caption(f"Dispensazioni: {df.shape[0]:,} righe • DDD: {tab_ddd.shape[0]:,} righe")

    # -------------------------------
    # Form (come v9)
    # -------------------------------
    with st.form("setup"):
        col1, col2, col3 = st.columns(3)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            atc_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns)
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
        with col2:
            ddd_col = st.selectbox("Colonna DDD dispensate", df.columns)
            atc_ddd_col = st.selectbox("Colonna ATC nella tabella DDD", tab_ddd.columns)
            ddd_std_col = st.selectbox("Colonna DDD_standard nella tabella DDD", tab_ddd.columns)
        with col3:
            cutoff_naive = st.date_input("📅 Data indice (per selezionare naïve)")
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
            soglia = st.number_input("Soglia aderenza (PDC su persistenza)", min_value=0.0, max_value=1.0, value=0.80, step=0.05, format="%.2f")
        st.markdown("---")
        col4, col5 = st.columns(2)
        with col4:
            naive_scope = st.radio("Selezione naïve", ["Per paziente", "Per paziente+ATC"], horizontal=True)
        with col5:
            unit_scope = st.radio("Unità di analisi PDC", ["Per paziente (ATC principale)", "Per paziente+ATC"], horizontal=True)
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        # -------------------------------
        # Parse e merge (come v9)
        # -------------------------------
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        _check_unique(tab_ddd, atc_ddd_col)
        tab2 = tab_ddd[[atc_ddd_col, ddd_std_col]].drop_duplicates(subset=[atc_ddd_col])
        df = df.merge(tab2, left_on=atc_col, right_on=atc_ddd_col, how="left")
        df = df.rename(columns={ddd_std_col: "DDD_standard"})
        if df["DDD_standard"].isna().any():
            st.warning("⚠️ Alcuni ATC non hanno corrispondenza nella tabella DDD. Le relative righe saranno trattate come DDD=0.")
        df["DDD_standard"] = _safe_numeric(df["DDD_standard"]).replace([float("inf"), -float("inf")], 0)
        df[ddd_col] = _safe_numeric(df[ddd_col])

        invalid_std = (df["DDD_standard"] <= 0).sum()
        if invalid_std > 0:
            st.info(f"ℹ️ {invalid_std} righe con DDD_standard ≤ 0: il contributo in giorni_coperti sarà 0.")

        # -------------------------------
        # Selezione naïve (come v9)
        # -------------------------------
        cutoff = pd.to_datetime(cutoff_naive)
        if naive_scope == "Per paziente":
            first_disp = df.groupby(id_col)[date_col].min().reset_index().rename(columns={date_col: "__first_date"})
            naive_ids = first_disp[first_disp["__first_date"] >= cutoff][id_col]
            df = df[df[id_col].isin(naive_ids)].merge(first_disp, on=id_col, how="left")
        else:
            first_disp = df.groupby([id_col, atc_col])[date_col].min().reset_index().rename(columns={date_col: "__first_date"})
            df = df.merge(first_disp, on=[id_col, atc_col], how="left")
            df = df[df["__first_date"] >= cutoff]

        if df.empty:
            st.error("Nessun paziente/ATC naïve secondo i criteri selezionati.")
            st.stop()

        # -------------------------------
        # Prepara eventi (come v9)
        # -------------------------------
        df["giorni_coperti"] = 0.0
        mask_pos = df["DDD_standard"] > 0
        df.loc[mask_pos, "giorni_coperti"] = (df.loc[mask_pos, ddd_col] / df.loc[mask_pos, "DDD_standard"]).fillna(0.0)
        df["__date"] = df[date_col]

        # -------------------------------
        # Calcolo PDC = su persistenza (v10)
        # -------------------------------
        risultati = []
        if unit_scope == "Per paziente (ATC principale)":
            for pid, s in df.sort_values("__date").groupby(id_col):
                start = s["__date"].min()
                pdc, giorni_pers = calcola_pdc_persistenza(s[["__date", "giorni_coperti"]], start, periodo)
                atc_principale = s[atc_col].mode().iloc[0] if not s[atc_col].mode().empty else None
                risultati.append({id_col: pid, "ATC_unit": atc_principale, "PDC": pdc, "Persistenza_giorni": int(giorni_pers), "Durata": int(periodo)})
        else:
            for (pid, atc), s in df.sort_values("__date").groupby([id_col, atc_col]):
                start = s["__date"].min()
                pdc, giorni_pers = calcola_pdc_persistenza(s[["__date", "giorni_coperti"]], start, periodo)
                risultati.append({id_col: pid, "ATC_unit": atc, "PDC": pdc, "Persistenza_giorni": int(giorni_pers), "Durata": int(periodo)})

        aderenza = pd.DataFrame(risultati)
        aderenza["Aderente"] = aderenza["PDC"] >= soglia

        # -------------------------------
        # Riepiloghi (come v9, ma su PDC persistente)
        # -------------------------------
        st.subheader("📊 PDC (su persistenza) – risultati unità di analisi")
        st.dataframe(aderenza, use_container_width=True)

        st.subheader("📊 Riepilogo per ATC_unit")
        riepilogo = aderenza.groupby("ATC_unit").agg(
            N_unit=("PDC", "count"),
            N_aderenti=("Aderente", "sum"),
            PDC_medio=("PDC", "mean"),
            PDC_std=("PDC", "std"),
            P50=("PDC", "median"),
            P10=("PDC", lambda s: s.quantile(0.10)),
            P90=("PDC", lambda s: s.quantile(0.90)),
            PDC_min=("PDC", "min"),
            PDC_max=("PDC", "max"),
        ).reset_index()
        riepilogo["%_aderenti"] = (100 * riepilogo["N_aderenti"] / riepilogo["N_unit"]).round(1)
        st.dataframe(riepilogo, use_container_width=True)

        # -------------------------------
        # Grafici (come v9, su PDC persistente)
        # -------------------------------
        st.subheader("📈 Distribuzione PDC per ATC_unit")
        if not aderenza.empty:
            order = aderenza.groupby("ATC_unit")["PDC"].median().sort_values().index
            fig = px.box(
                aderenza,
                x=pd.Categorical(aderenza["ATC_unit"], categories=order, ordered=True),
                y="PDC",
                points="all",
                title=f"Distribuzione PDC (su persistenza) per ATC_unit – soglia aderente = {soglia:.2f}",
                labels={"x": "ATC_unit", "PDC": "PDC (su persistenza)"}
            )
            st.plotly_chart(fig, use_container_width=True)

        # -------------------------------
        # Download (stesso schema v9)
        # -------------------------------
        st.subheader("📥 Scarica risultati")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            aderenza.to_excel(writer, index=False, sheet_name="PDC_unita")
            riepilogo.to_excel(writer, index=False, sheet_name="Riepilogo_ATC_unit")
        st.download_button(
            label="💾 Scarica risultati (Excel)",
            data=buffer.getvalue(),
            file_name="risultati_aderenza_v11_persistenza.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

else:
    st.info("Carica entrambi i file per continuare.")
