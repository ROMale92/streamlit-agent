
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io

st.set_page_config(layout="wide")
st.title("Sankey terapeutico – con esiti e cutoff follow-up")

file = st.file_uploader("① Carica file Excel con dispensazioni", type=["xlsx"])

if file:
    df = pd.read_excel(file)
    st.success("File caricato.")
    st.dataframe(df.head())

    with st.form("parametri"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna ID paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica (ATC/classe)", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            data_indice = st.date_input("Data indice (pazienti naïve)")
            cutoff_followup = st.date_input("Data cutoff follow-up")
        invia = st.form_submit_button("Visualizza Sankey")

    if invia:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        prima_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = prima_disp[prima_disp[date_col] >= pd.to_datetime(data_indice)][id_col]
        df = df[df[id_col].isin(naive_ids)].copy()

        df = df.sort_values([id_col, date_col])
        df["Linea"] = df.groupby(id_col)[cat_col].transform(lambda x: x.ne(x.shift()).cumsum())
        df["Linea"] = df["Linea"].astype(int)
        df["Terapia_linea"] = df[cat_col] + " (Linea " + df["Linea"].astype(str) + ")"

        # Calcola esito
        last_date = df.groupby(id_col)[date_col].max().reset_index()
        last_date["Esito"] = last_date[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(cutoff_followup) else "Perso al follow-up"
        )
        df = df.merge(last_date[[id_col, "Esito"]], on=id_col, how="left")

        # Crea transizioni tra linee
        max_linea = df["Linea"].max()
        sankey_data = []

        for i in range(1, max_linea):
            step = df[df["Linea"].isin([i, i+1])]
            pivot = step.pivot_table(index=id_col, columns="Linea", values="Terapia_linea", aggfunc="first").dropna()
            transizioni = pivot.groupby([i, i+1]).size().reset_index(name="Count")
            sankey_data.append(transizioni)

        # Transizioni finali: ultima terapia → esito
        last_therapy = df.groupby(id_col).agg({
            "Terapia_linea": "last",
            "Esito": "last"
        }).reset_index()
        esiti = last_therapy.groupby(["Terapia_linea", "Esito"]).size().reset_index(name="Count")
        esiti.columns = [max_linea + 1, max_linea + 2, "Count"]
        sankey_data.append(esiti)

        # Crea dataframe finale per Sankey
        df_sankey = pd.concat(sankey_data, ignore_index=True)
        all_labels = pd.concat([df_sankey.iloc[:, 0], df_sankey.iloc[:, 1]]).unique().tolist()
        df_sankey["source"] = df_sankey.iloc[:, 0].apply(lambda x: all_labels.index(x))
        df_sankey["target"] = df_sankey.iloc[:, 1].apply(lambda x: all_labels.index(x))

        # Sankey Plot
        fig = go.Figure(go.Sankey(
            node=dict(label=all_labels, pad=15, thickness=20),
            link=dict(source=df_sankey["source"], target=df_sankey["target"], value=df_sankey["Count"])
        ))

        st.subheader("📈 Sankey terapeutico con esiti")
        st.plotly_chart(fig, use_container_width=True)

        # Esporta Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df[[id_col, cat_col, date_col, "Linea", "Terapia_linea", "Esito"]].to_excel(writer, index=False, sheet_name="Linee_terapeutiche")
            df_sankey.to_excel(writer, index=False, sheet_name="Flussi_sankey")

        st.download_button("⬇️ Scarica risultati in Excel", data=buffer.getvalue(), file_name="sankey_esiti.xlsx")
