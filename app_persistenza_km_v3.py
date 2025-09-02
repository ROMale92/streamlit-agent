
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from lifelines.statistics import logrank_test, multivariate_logrank_test

st.set_page_config(layout="wide")
st.title("Analisi persistenza terapeutica – Kaplan–Meier con test log-rank")

# Upload file dispensazioni
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni", type=["xlsx"])

if file_disp:
    df = pd.read_excel(file_disp)
    st.success("✅ File caricato")

    with st.expander("Anteprima dati", expanded=False):
        st.dataframe(df.head())

    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            strat_col = st.selectbox("Variabile di stratificazione", [c for c in df.columns if c != id_col])
        with col2:
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        # Calcola per paziente
        results = []
        for pid, g in df.groupby(id_col):
            start = g[date_col].min()
            end = g[date_col].max()
            days = (end - start).days

            # time normalizzato al periodo
            time = min(days, periodo)
            # event: 1 se ha interrotto prima del periodo, 0 se persistente
            event = 1 if days < periodo else 0

            strat = g[strat_col].mode().iloc[0] if not g[strat_col].mode().empty else "NA"
            results.append({"paziente": pid, "time": time, "event": event, strat_col: strat})

        km_df = pd.DataFrame(results)

        # Crea curva KM manualmente
        fig = go.Figure()
        summary_rows = []

        for strat, g in km_df.groupby(strat_col):
            n = len(g)
            g = g.sort_values("time")
            times = [0]
            surv = [1.0]
            at_risk = n
            s = 1.0

            for t, e in zip(g["time"], g["event"]):
                if e == 1:  # evento
                    s *= (at_risk - 1) / at_risk
                at_risk -= 1
                times.append(t)
                surv.append(s)

            if times[-1] < periodo:
                times.append(periodo)
                surv.append(s)

            fig.add_trace(go.Scatter(x=times, y=surv, mode="lines+markers",
                                     line_shape="hv", name=str(strat)))

            # summary a 180, 365, 730 se <= periodo
            for pt in [180, 365, 730]:
                if pt <= periodo:
                    surv_val = surv[max(i for i, tt in enumerate(times) if tt <= pt)]
                    summary_rows.append({"Strato": strat, "Giorni": pt, "Persistenti": surv_val*100})

        st.subheader("📈 Curva Kaplan–Meier di persistenza")
        fig.update_layout(xaxis_title="Giorni", yaxis_title="Probabilità di persistenza",
                          yaxis=dict(range=[0,1]))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("📊 Riepilogo punti fissi")
        summary = pd.DataFrame(summary_rows)
        st.dataframe(summary)

        # --------- Test log-rank ---------
        groups = km_df[strat_col].unique()
        if len(groups) == 2:
            g1, g2 = groups
            df1 = km_df[km_df[strat_col] == g1]
            df2 = km_df[km_df[strat_col] == g2]
            result = logrank_test(df1["time"], df2["time"], df1["event"], df2["event"])
            st.subheader("📊 Test log-rank (2 gruppi)")
            st.write(f"Statistic χ² = {result.test_statistic:.3f}, p-value = {result.p_value:.4f}")
        elif len(groups) > 2:
            result = multivariate_logrank_test(km_df["time"], km_df[strat_col], km_df["event"])
            st.subheader("📊 Test log-rank (multi-gruppo)")
            st.write(f"Statistic χ² = {result.test_statistic:.3f}, p-value = {result.p_value:.4f}")
        else:
            st.info("Serve almeno 2 gruppi per eseguire il test log-rank.")
else:
    st.info("Carica un file Excel per iniziare.")
