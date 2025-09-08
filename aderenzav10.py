import streamlit as st
import pandas as pd

st.title("Calcolo aderenza per intervalli")

# Caricamento file
file = st.file_uploader("Carica il file delle dispensazioni (Excel o CSV)", type=["xlsx", "csv"])

if file:
    # Lettura file
    if file.name.endswith(".csv"):
        df = pd.read_csv(file, sep=";|,", engine="python")
    else:
        df = pd.read_excel(file)

    st.write("✅ File caricato con successo", df.head())

    # Selezione colonne
    col_cf = st.selectbox("Colonna codice fiscale", df.columns)
    col_principio = st.selectbox("Colonna principio attivo", df.columns)
    col_data = st.selectbox("Colonna data erogazione", df.columns)
    col_ddd = st.selectbox("Colonna DDD erogate", df.columns)
    col_atc = st.selectbox("Colonna ATC (opzionale)", ["(Nessuna)"] + list(df.columns))

    # Periodo osservazione
    giorni_osservazione = st.number_input("Periodo di osservazione (giorni)", value=365)

    # Conversione date
    df[col_data] = pd.to_datetime(df[col_data], dayfirst=True, errors="coerce")

    # Calcolo PDC per ogni paziente e principio
    def calcola_pdc(gruppo):
        giorni_coperti = gruppo[col_ddd].sum()
        pdc = min(giorni_coperti / giorni_osservazione, 1)
        return pd.Series({
            "giorni_coperti": giorni_coperti,
            "PDC": pdc
        })

    group_cols = [col_cf, col_principio]
    if col_atc != "(Nessuna)":
        group_cols.append(col_atc)

    risultati = df.groupby(group_cols).apply(calcola_pdc).reset_index()

    # Media per ATC se disponibile
    if col_atc != "(Nessuna)":
        media_atc = risultati.groupby(col_atc)["PDC"].mean().reset_index()
        st.subheader("📊 Media PDC per ATC")
        st.dataframe(media_atc)

    # Output
    st.subheader("📑 Risultati per paziente e principio attivo")
    st.dataframe(risultati)

    # Download
    st.download_button("Scarica risultati (Excel)", risultati.to_csv(index=False).encode("utf-8"), "aderenza.csv", "text/csv")
