
import streamlit as st
import pandas as pd
import io

st.set_page_config(layout="wide")
st.title("Analisi linee terapeutiche per paziente – con Tabella 1")

file = st.file_uploader("① Carica file Excel con dispensazioni", type=["xlsx"])

if file:
    df = pd.read_excel(file)
    st.success("File caricato.")
    st.dataframe(df.head())

    with st.form("parametri"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna ID paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica (es. ATC o classe)", df.columns)
            ex_col = st.selectbox("Colonna sesso", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            age_col = st.selectbox("Colonna età", df.columns)
            data_indice = st.date_input("Data indice (pazienti naïve)")
        invia = st.form_submit_button("Esegui analisi")

    if invia:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])

        # Filtra pazienti naïve
        prima_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = prima_disp[prima_disp[date_col] >= pd.to_datetime(data_indice)][id_col]
        df = df[df[id_col].isin(naive_ids)].copy()

        # Ordina e calcola linee terapeutiche
        df = df.sort_values([id_col, date_col])
        df["Linea"] = df.groupby(id_col)[cat_col].transform(lambda x: x.ne(x.shift()).cumsum())
        df["Linea"] = df["Linea"].astype(int)
        df["Terapia_linea"] = df[cat_col] + " (Linea " + df["Linea"].astype(str) + ")"

        st.subheader("📊 Linee terapeutiche")
        st.dataframe(df[[id_col, date_col, cat_col, "Linea", "Terapia_linea"]])

        # Selezione linee
        linee_disponibili = sorted(df["Linea"].unique())
        linee_sel = st.multiselect("Seleziona le linee da includere in Tabella 1", options=linee_disponibili, default=[1])
        df_linee = df[df["Linea"].isin(linee_sel)]

        # Tabella 1
        st.subheader("📋 Tabella 1 – Caratteristiche pazienti per categoria")
        tab1 = df_linee.groupby(cat_col).agg(
            N_pazienti=(id_col, "nunique"),
            Perc_maschi=(ex_col, lambda x: round((x == 'M').mean()*100, 2)),
            Età_media=(age_col, "mean"),
            Età_mediana=(age_col, "median"),
            Età_min=(age_col, "min"),
            Età_max=(age_col, "max")
        ).reset_index()
        st.dataframe(tab1)

        # Excel export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df[[id_col, cat_col, date_col, "Linea", "Terapia_linea"]].to_excel(writer, index=False, sheet_name="Linee_terapeutiche")
            tab1.to_excel(writer, index=False, sheet_name="Tabella1")

        st.download_button("⬇️ Scarica risultati in Excel", data=buffer.getvalue(), file_name="linee_terapeutiche_tab1.xlsx")
