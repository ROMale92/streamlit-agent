
import streamlit as st
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("Analisi aderenza terapeutica (PDC)")

# UPLOAD FILE DISPENSAZIONI
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"], key="disp")
# UPLOAD FILE DDD
file_ddd = st.file_uploader("📁 Carica file Excel con tabella DDD (ATC, DDD_standard)", type=["xlsx"], key="ddd")

if file_disp and file_ddd:
    df = pd.read_excel(file_disp)
    tab_ddd = pd.read_excel(file_ddd)
    st.success("✅ File caricati!")

    # FORM INPUT
    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            atc_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns)
            ddd_col = st.selectbox("Colonna DDD dispensate", df.columns)
            atc_ddd_col = st.selectbox("Colonna ATC nella tabella DDD", tab_ddd.columns)
            ddd_std_col = st.selectbox("Colonna DDD_standard nella tabella DDD", tab_ddd.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_naive = st.date_input("📅 Data indice (per selezionare naïve)")
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        # PARSING DATE
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        # MERGE con tabella DDD
        df = df.merge(tab_ddd[[atc_ddd_col, ddd_std_col]], left_on=atc_col, right_on=atc_ddd_col, how="left")
        df = df.rename(columns={ddd_std_col: "DDD_standard"})
        if df["DDD_standard"].isna().any():
            st.warning("⚠️ Attenzione: alcuni ATC non hanno corrispondenza nella tabella DDD.")

        # IDENTIFICA NAÏVE
        first_disp = df.groupby(id_col)[date_col].min().reset_index().rename(columns={date_col: "prima_data"})
        naive_ids = first_disp[first_disp["prima_data"] >= pd.to_datetime(cutoff_naive)][id_col]
        df = df[df[id_col].isin(naive_ids)]
        df = df.merge(first_disp, on=id_col, how="left")

        # CALCOLA FINE FINESTRA PERIODO OSSERVAZIONE
        df["fine_finestra"] = df["prima_data"] + pd.to_timedelta(periodo, unit="D")
        df = df[df[date_col] <= df["fine_finestra"]]

        # ORDINAMENTO
        df = df.sort_values(by=[id_col, date_col])

        # CALCOLO GIORNI COPERTURA dalle DDD dispensate
        df["giorni_copertura"] = df[ddd_col] / df["DDD_standard"]

        # CALCOLO PDC PER PAZIENTE
        pdc = df.groupby(id_col).agg({
            "giorni_copertura": "sum",
            "prima_data": "min",
            date_col: "max",
            atc_col: lambda x: x.mode()[0] if not x.mode().empty else None
        }).reset_index()

        pdc["periodo_osservato"] = (pdc[date_col] - pdc["prima_data"]).dt.days + 1
        pdc["PDC"] = pdc["giorni_copertura"] / pdc["periodo_osservato"]
        pdc["Aderente"] = pdc["PDC"] >= 0.8

        # RIEPILOGO PER ATC
        riepilogo = pdc.groupby(atc_col).agg({
            id_col: "count",
            "Aderente": "sum",
            "PDC": "mean"
        }).reset_index().rename(columns={id_col: "N_pazienti", "Aderente": "N_aderenti", "PDC": "PDC_medio"})
        riepilogo["%_aderenti"] = (riepilogo["N_aderenti"] / riepilogo["N_pazienti"] * 100).round(1)

        # MOSTRA RISULTATI
        st.subheader("📊 PDC per paziente")
        st.dataframe(pdc)

        st.subheader("📊 Riepilogo per ATC")
        st.dataframe(riepilogo)

        # DOWNLOAD RISULTATI
        st.subheader("📥 Scarica risultati")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pdc.to_excel(writer, index=False, sheet_name="PDC pazienti")
            riepilogo.to_excel(writer, index=False, sheet_name="Riepilogo ATC")
        st.download_button(
            label="💾 Scarica risultati (Excel)",
            data=buffer.getvalue(),
            file_name="risultati_aderenza.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
