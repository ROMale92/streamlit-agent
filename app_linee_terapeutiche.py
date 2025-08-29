
import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("Analisi linee terapeutiche per paziente")

# Upload
file = st.file_uploader("① Carica file Excel con dispensazioni", type=["xlsx"])

if file:
    df = pd.read_excel(file)
    st.success("File caricato. Visualizza le prime righe:")
    st.dataframe(df.head())

    # Form
    with st.form("selezione"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna ID paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC o classe)", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            data_indice = st.date_input("Data indice (per selezione pazienti naïve)")
        invia = st.form_submit_button("Analizza linee terapeutiche")

    if invia:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])

        # Pazienti naïve = prima dispensazione ≥ data_indice
        prima_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = prima_disp[prima_disp[date_col] >= pd.to_datetime(data_indice)][id_col]
        df = df[df[id_col].isin(naive_ids)].copy()

        # Ordina e calcola linee
        df = df.sort_values([id_col, date_col])
        df["Linea"] = df.groupby(id_col)[cat_col].transform(lambda x: x.ne(x.shift()).cumsum())
        df["Linea"] = df["Linea"].astype(int)
        df["Terapia_linea"] = df[cat_col] + " (Linea " + df["Linea"].astype(str) + ")"

        st.subheader("📊 Linee terapeutiche individuate")
        st.dataframe(df[[id_col, date_col, cat_col, "Linea", "Terapia_linea"]])

        # Export
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Scarica risultati (CSV)", csv, file_name="linee_terapeutiche.csv", mime="text/csv")
