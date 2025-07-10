import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import base64
from io import BytesIO

st.set_page_config(layout="wide")
st.title("🔎 Analisi prescrittiva farmaci antidiabetici")

# Upload file
uploaded_file = st.file_uploader("Carica il file Excel", type=["xlsx"])
if uploaded_file:
    df = pd.read_excel(uploaded_file)

    # Selezione colonne
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        id_col = st.selectbox("Identificativo paziente", df.columns)
    with col2:
        line_col = st.selectbox("Numero linea", df.columns)
    with col3:
        cat_col = st.selectbox("Categoria terapeutica", df.columns)
    with col4:
        date_col = st.selectbox("Data erogazione", df.columns)

    cutoff = st.date_input("📅 Seleziona cut-off di follow-up")

    # Pre-elaborazione
    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=[id_col, line_col, cat_col, date_col])
    df = df[df[date_col] <= pd.to_datetime(cutoff)]

    # Estrazione linee e costruzione flussi
    df['Linea'] = df[line_col].astype(str)
    df['Fonte'] = df[cat_col] + " (Linea " + df['Linea'] + ")"

    pazienti_finali = df.groupby(id_col).agg({'Linea': 'max', date_col: 'max'}).reset_index()
    pazienti_finali['Esito'] = pazienti_finali[date_col].apply(lambda x: "In trattamento" if x >= pd.to_datetime(cutoff) else "Perso al follow-up")

    df = df.merge(pazienti_finali[[id_col, 'Esito']], on=id_col)

    # Creazione flussi Sankey
    flows = []
    for i in sorted(df['Linea'].unique()):
        df_line = df[df['Linea'] == i]
        next_line = str(int(i) + 1)
        df_next = df[df['Linea'] == next_line]

        flow = df_line.groupby(['Fonte', id_col]).size().reset_index().groupby('Fonte').size()
        flows.append(flow)

    sankey_df = df.groupby([cat_col, 'Linea', 'Esito']).size().reset_index(name='value')
    sankey_df['source'] = sankey_df[cat_col] + " (Linea " + sankey_df['Linea'] + ")"
    sankey_df['target'] = sankey_df['Esito']

    labels = list(pd.concat([sankey_df['source'], sankey_df['target']]).unique())
    label_index = {label: i for i, label in enumerate(labels)}
    sankey_df['source_id'] = sankey_df['source'].map(label_index)
    sankey_df['target_id'] = sankey_df['target'].map(label_index)

    fig = go.Figure(data=[go.Sankey(
        node=dict(label=labels, pad=15, thickness=20),
        link=dict(
            source=sankey_df['source_id'],
            target=sankey_df['target_id'],
            value=sankey_df['value']
        )
    )])
    st.plotly_chart(fig, use_container_width=True)

    # Esportazione Excel
    def convert_df(df):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
        return output.getvalue()

    excel_bytes = convert_df(df)
    b64 = base64.b64encode(excel_bytes).decode()
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="risultati.xlsx">📥 Scarica risultati in Excel</a>'
    st.markdown(href, unsafe_allow_html=True)
