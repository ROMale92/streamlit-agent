
import streamlit as st
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("Analisi aderenza terapeutica (PDC) basata su DDD")

file = st.file_uploader("① Carica file Excel con dispensazioni", type=["xlsx"])
ddd_file = st.file_uploader("② Carica file con DDD standard (es. ATC/DDD OMS)", type=["xlsx"])

if file and ddd_file:
    df = pd.read_excel(file)
    ddd_df = pd.read_excel(ddd_file)
    st.success("File caricati.")
    st.dataframe(df.head())

    with st.form("parametri"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna ID paziente", df.columns)
            atc_col = st.selectbox("Colonna ATC/categoria terapeutica", df.columns)
            atc_ref_col = st.selectbox("Colonna ATC nel file DDD", ddd_df.columns)
            ddd_ref_col = st.selectbox("Colonna DDD standard", ddd_df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            ddd_disp_col = st.selectbox("Colonna DDD dispensate", df.columns)
            sesso_col = st.selectbox("Colonna sesso", df.columns)
            eta_col = st.selectbox("Colonna età", df.columns)
        invia = st.form_submit_button("Esegui analisi")

    if invia:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df[ddd_disp_col] = pd.to_numeric(df[ddd_disp_col], errors="coerce")
        df = df.dropna(subset=[date_col, ddd_disp_col])

        # Mappa DDD standard dal dizionario
        ddd_map = dict(zip(ddd_df[atc_ref_col], ddd_df[ddd_ref_col]))
        df["DDD_standard"] = df[atc_col].map(ddd_map)
        df["Giorni_coperti"] = df[ddd_disp_col] / df["DDD_standard"]

        # Calcolo PDC
        aderenza = df.groupby(id_col).agg(
            ATC_principale=(atc_col, lambda x: x.mode().iloc[0]),
            DDD_totali=("Giorni_coperti", "sum"),
            Prima_disp=(date_col, "min"),
            Ultima_disp=(date_col, "max"),
            Sesso=(sesso_col, "first"),
            Età=(eta_col, "first")
        ).reset_index()
        aderenza["Durata"] = (aderenza["Ultima_disp"] - aderenza["Prima_disp"]).dt.days + 1
        aderenza["PDC"] = aderenza["DDD_totali"] / aderenza["Durata"]
        aderenza["PDC"] = aderenza["PDC"].clip(upper=1.0)
        aderenza["Aderente"] = aderenza["PDC"] >= 0.8

        st.subheader("📊 Tabella PDC per paziente")
        st.dataframe(aderenza)

        # Tabella 1
        st.subheader("📋 Tabella 1 – Caratteristiche pazienti per ATC")
        tab1 = aderenza.groupby("ATC_principale").agg(
            N_pazienti=(id_col, "count"),
            Perc_maschi=("Sesso", lambda x: round((x == "M").mean()*100, 2)),
            Età_media=("Età", "mean"),
            Età_mediana=("Età", "median"),
            Età_min=("Età", "min"),
            Età_max=("Età", "max"),
            PDC_medio=("PDC", "mean"),
            PDC_std=("PDC", "std"),
            Aderenti=( "Aderente", "sum")
        ).reset_index()
        tab1["%_aderenti"] = round(tab1["Aderenti"] / tab1["N_pazienti"] * 100, 2)
        st.dataframe(tab1)

        # Esporta Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            aderenza.to_excel(writer, index=False, sheet_name="PDC_per_paziente")
            tab1.to_excel(writer, index=False, sheet_name="Tabella1")

        st.download_button("⬇️ Scarica risultati in Excel", data=buffer.getvalue(), file_name="aderenza_ddd.xlsx")
