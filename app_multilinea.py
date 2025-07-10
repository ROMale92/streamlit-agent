import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import io

st.set_page_config(layout="wide")
st.title("Analisi prescrittiva farmaci - da dispensazioni singole")

file = st.file_uploader("Carica file Excel con dispensazioni singole", type=["xlsx"])
if file:
    df = pd.read_excel(file)
    st.success("File caricato correttamente!")
    st.dataframe(df.head())

    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            cat_col = st.selectbox("Colonna categoria terapeutica", df.columns)
        with col2:
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            cutoff_date = st.date_input("Data indice (cut-off naïve)")
            followup_cutoff = st.date_input("Data cut-off follow-up", value=pd.to_datetime("2024-09-30"))
            ex_col = st.selectbox("Colonna per il sesso", options=df.columns)
            age_col = st.selectbox("Colonna per l'età", options=df.columns)

        submit = st.form_submit_button("Procedi")

    if submit:
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)
        df = df.dropna(subset=[date_col])
        first_disp = df.groupby(id_col)[date_col].min().reset_index()
        naive_ids = first_disp[first_disp[date_col] >= pd.to_datetime(cutoff_date)][id_col]
        df = df[df[id_col].isin(naive_ids)]

        df = df.sort_values([id_col, date_col])
        df["Linea"] = (
            df.groupby(id_col)[cat_col]
              .transform(lambda x: x.ne(x.shift()).cumsum())
        )
        df["Linea"] = df["Linea"].astype(str)
        df["Terapia"] = df[cat_col] + " (Linea " + df["Linea"] + ")"

        last_dates = df.groupby(id_col)[date_col].max().reset_index()
        last_dates["Esito"] = last_dates[date_col].apply(
            lambda x: "In trattamento" if x >= pd.to_datetime(followup_cutoff) else "Perso al follow-up"
        )
        df = df.merge(last_dates[[id_col, "Esito"]], on=id_col, how="left")

        # Sankey su tutte le linee
        st.subheader("Flussi terapeutici (Sankey)")
        sankey_data = []
        max_line = df["Linea"].astype(int).max()
        for i in range(1, max_line):
            step = df[df["Linea"].isin([str(i), str(i + 1)])]
            step_pivot = step.pivot_table(index=id_col, columns="Linea", values="Terapia", aggfunc="first").dropna()
            if not step_pivot.empty:
                step_df = step_pivot.groupby([str(i), str(i + 1)]).size().reset_index(name="Count")
                sankey_data.append(step_df)

        last_step = df.groupby(id_col).agg({"Linea": "max", "Terapia": "last", "Esito": "last"}).reset_index()
        last_df = last_step.groupby(["Terapia", "Esito"]).size().reset_index(name="Count")
        sankey_data.append(last_df.rename(columns={"Terapia": str(max_line + 1), "Esito": str(max_line + 2)}))

        if sankey_data:
            sankey_df = pd.concat(sankey_data)
            all_labels = pd.concat([sankey_df.iloc[:, 0], sankey_df.iloc[:, 1]]).unique().tolist()
            sankey_df["source"] = sankey_df[sankey_df.columns[0]].apply(lambda x: all_labels.index(x))
            sankey_df["target"] = sankey_df[sankey_df.columns[1]].apply(lambda x: all_labels.index(x))

            fig = go.Figure(go.Sankey(
                node=dict(label=all_labels, pad=15, thickness=20),
                link=dict(source=sankey_df["source"], target=sankey_df["target"], value=sankey_df["Count"])
            ))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Non ci sono abbastanza transizioni terapeutiche per generare un Sankey.")

        # Selezione linee da analizzare
        st.subheader("Analisi dettaglia
