import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io

st.set_page_config(layout="wide")
st.title("Analisi prescrittiva farmaci - da dispensazioni singole")

# Upload file
file = st.file_uploader("Carica file Excel con dispensazioni singole", type=["xlsx"])
if file:
    df = pd.read_excel(file)
    st.success("File caricato correttamente!")
    st.dataframe(df.head())

    # Selezione colonne
    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_date = st.date_input("Data indice (cut-off naïve)")

        submit = st.form_submit_button("Procedi")

    if submit:
        # Conversione data
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)

        # Pazienti naïve
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_date)][id_col]
        df = df[df[id_col].isin(naive_ids)]
        st.info(f"Pazienti naïve dopo {cutoff_date}: {naive_ids.nunique()}")

        # Ordina e assegna linea
        df = df.sort_values([id_col, date_col])
        df["Linea"] = (
            df.groupby(id_col)[cat_col]
              .transform(lambda x: x.ne(x.shift()).cumsum())
        )
        df["Linea"] = df["Linea"].astype(str)

        # Etichetta terapia
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        # Esito follow-up
        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime("2024-09-30") else "Perso al follow-up"
        )
        df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

        # Sankey
        sankey_data = []
        max_line = df["Linea"].astype(int).max()
        for i in range(1, max_line):
            step = df[df["Linea"].isin([str(i), str(i + 1)])]
            step_pivot = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
            step_df = step_pivot.groupby([str(i), str(i + 1)]).size().reset_index(name="Count")
            sankey_data.append(step_df)

        # Ultimo step verso esito
        last_step = df.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
        last_df = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
        sankey_data.append(last_df.rename(columns={"Terapia": str(max_line + 1), "Esito": str(max_line + 2)}))

        sankey_df = pd.concat(sankey_data)
        all_labels = pd.concat([sankey_df.iloc[:, 0], sankey_df.iloc[:, 1]]).unique().tolist()
        sankey_df["source"] = sankey_df[sankey_df.columns[0]].apply(lambda x: all_labels.index(x))
        sankey_df["target"] = sankey_df[sankey_df.columns[1]].apply(lambda x: all_labels.index(x))

        fig = go.Figure(go.Sankey(
            node=dict(label=all_labels, pad=15, thickness=20),
            link=dict(source=sankey_df["source"], target=sankey_df["target"], value=sankey_df["Count"])
        ))
        st.plotly_chart(fig, use_container_width=True)

        # Download HTML interattivo
        html_bytes = fig.to_html().encode()
        st.download_button("Scarica Sankey interattivo", html_bytes, file_name="sankey.html")

        # Persistenza
        days_df = df.groupby([id_col, "Linea"]).agg(
            Prima_disp=(date_col, "min"),
            Ultima_disp=(date_col, "max")
        ).reset_index()
        days_df["Durata_trattamento"] = (days_df["Ultima_disp"] - days_df["Prima_disp"]).dt.days

        # Aderenza PDC
        pdc_df = df.copy()
        pdc_df["Mese"] = df[date_col].dt.to_period("M")
        aderenza = (
            pdc_df.groupby([id_col, "Linea"])[["Mese"]]
            .nunique()
            .rename(columns={"Mese": "Mesi coperti"})
            .reset_index()
        )
        aderenza["Durata mesi"] = (
            df.groupby([id_col, "Linea"])[date_col]
            .agg(lambda x: (x.max() - x.min()).days // 30 + 1)
            .values
        )
        aderenza["PDC"] = aderenza["Mesi coperti"] / aderenza["Durata mesi"]
        aderenza["Aderente (≥0.8)"] = aderenza["PDC"] >= 0.8

        # Esportazione Excel
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Dispensazioni filtrate")
            days_df.to_excel(writer, index=False, sheet_name="Persistenza")
            aderenza.to_excel(writer, index=False, sheet_name="Aderenza_PDC")
        st.download_button("Scarica risultati in Excel", excel_buffer.getvalue(), file_name="output_risultati.xlsx")
