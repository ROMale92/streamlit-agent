
import streamlit as st
import plotly.graph_objects as go

st.title("Sankey test - stabile")

labels = ['A (Linea 1)', 'B (Linea 2)', 'C (Linea 3)', 'In trattamento', 'Perso al follow-up']
source = [0, 1, 2]
target = [1, 2, 3]
value = [10, 5, 8]

fig = go.Figure(go.Sankey(
    node=dict(
        pad=15,
        thickness=20,
        line=dict(color="black", width=0.5),
        label=[str(label) for label in labels]
    ),
    link=dict(
        source=[int(x) for x in source],
        target=[int(x) for x in target],
        value=[float(x) for x in value]
    )
))

st.plotly_chart(fig, use_container_width=True)
