
import streamlit as st
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("Analisi aderenza terapeutica (PDC)")

file = st.file_uploader("📁 Carica file Excel con dispensazioni singole", type=["xlsx"])
if file:
    df = pd.read_excel(file)
    st.success("✅ File caricato!")

    # FORM INPUT
    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC)", df.columns)
            ddd_col = st.selectbox("Colonna DDD dispensate", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_naive = st.date_input("📅 Data indice (per selezionare naïve)")
            ddd_std = st.number_input("DDD giornaliera standard", min_value=0.1, value=1.0, step=0.1)
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        # PARSING DATE
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

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
        df["giorni_copertura"] = df[ddd_col] / ddd_std

        # CALCOLO PDC PER PAZIENTE
        pdc = df.groupby(id_col).agg({
            "giorni_copertura": "sum",
            "prima_data": "min",
            date_col: "max",
            cat_col: lambda x: x.mode()[0] if not x.mode().empty else None
        }).reset_index()

        pdc["periodo_osservato"] = (pdc[date_col] - pdc["prima_data"]).dt.days + 1
        pdc["PDC"] = pdc["giorni_copertura"] / pdc["periodo_osservato"]
        pdc["Aderente"] = pdc["PDC"] >= 0.8

        # RIEPILOGO PER ATC
        riepilogo = pdc.groupby(cat_col).agg({
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
