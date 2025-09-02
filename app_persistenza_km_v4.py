
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.stats import chi2

st.set_page_config(layout="wide")
st.title("Persistenza terapeutica – Kaplan–Meier (Plotly) + Log-rank multi-gruppo (SciPy)")

# -------------------- Helpers --------------------
def build_patient_times(df, id_col, date_col, strat_col, period):
    # From raw dispensations, build per-patient summary: time, event, group.
    rows = []
    for pid, g in df.groupby(id_col):
        start = g[date_col].min()
        end = g[date_col].max()
        days = (end - start).days

        time = int(min(days, period))
        event = 1 if days < period else 0  # interruption if < period, else censored
        strat = g[strat_col].mode().iloc[0] if not g[strat_col].mode().empty else "NA"
        rows.append({"paziente": pid, "time": time, "event": int(event), strat_col: strat})
    return pd.DataFrame(rows)

def km_curve_from_times(times, events, period):
    # Compute KM step curve (times, survival) up to period.
    df = pd.DataFrame({"time": times, "event": events}).sort_values("time")
    df = df[df["time"] >= 0]
    S = 1.0
    t_coords = [0]
    s_coords = [1.0]
    at_risk = len(df)
    for t in df["time"].unique():
        if t > period:
            break
        d = int(df[(df["time"] == t) & (df["event"] == 1)].shape[0])
        c = int(df[(df["time"] == t) & (df["event"] == 0)].shape[0])
        if d > 0 and at_risk > 0:
            S *= (at_risk - d) / at_risk
        at_risk -= (d + c)
        t_coords.append(int(t))
        s_coords.append(S)
    if t_coords[-1] < period:
        t_coords.append(int(period))
        s_coords.append(S)
    return t_coords, s_coords

def logrank_multigroup(times, events, groups):
    # Multi-sample log-rank test without lifelines. Returns chi2 and p-value (df=k-1).
    df = pd.DataFrame({"time": times, "event": events, "group": groups})
    event_times = np.sort(df.loc[df["event"] == 1, "time"].unique())
    k = df["group"].nunique()
    if k < 2 or event_times.size == 0:
        return np.nan, np.nan, k

    group_order = sorted(df["group"].unique())
    k = len(group_order)

    O = np.zeros(k)
    E = np.zeros(k)
    V = np.zeros((k, k))

    for t in event_times:
        at_risk_mask = df["time"] >= t
        R = int(at_risk_mask.sum())
        if R <= 1:
            continue
        d = int(((df["time"] == t) & (df["event"] == 1)).sum())
        if d == 0 or d == R:
            continue

        Rg_series = df.groupby("group")["time"].apply(lambda s: int((s >= t).sum())).reindex(group_order).fillna(0)
        dg_series = df.groupby("group").apply(lambda g: int(((g["time"] == t) & (g["event"] == 1)).sum())).reindex(group_order).fillna(0)

        Rg = Rg_series.to_numpy(dtype=float)
        dg = dg_series.to_numpy(dtype=float)
        Eg = d * (Rg / R)

        common = d * (R - d) / (R**2 * (R - 1))
        V += np.diag(Rg * (R - Rg) * common)
        V -= np.outer(Rg, Rg) * common

        O += dg
        E += Eg

    D = O - E
    try:
        Vinv = np.linalg.pinv(V)
        chi2_stat = float(D.T @ Vinv @ D)
    except Exception:
        return np.nan, np.nan, k

    dfree = k - 1
    pval = 1 - chi2.cdf(chi2_stat, dfree)
    return chi2_stat, pval, k

# -------------------- UI --------------------
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
            strat_col = st.selectbox("Variabile di stratificazione (ATC / principio / categoria)", [c for c in df.columns if c != id_col])
        with col2:
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        km_df = build_patient_times(df, id_col, date_col, strat_col, int(periodo))

        fig = go.Figure()
        for strat, g in km_df.groupby(strat_col):
            t_coords, s_coords = km_curve_from_times(g["time"].to_numpy(), g["event"].to_numpy(), int(periodo))
            fig.add_trace(go.Scatter(x=t_coords, y=s_coords, mode="lines+markers", line_shape="hv", name=str(strat)))
        fig.update_layout(xaxis_title="Giorni", yaxis_title="Probabilità di persistenza", yaxis=dict(range=[0,1]))
        st.subheader("📈 Curva Kaplan–Meier di persistenza")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("📊 Riepilogo punti fissi")
        summary_rows = []
        for strat, g in km_df.groupby(strat_col):
            t_coords, s_coords = km_curve_from_times(g["time"].to_numpy(), g["event"].to_numpy(), int(periodo))
            for pt in [180, 365, 730]:
                if pt <= int(periodo):
                    idx = max(i for i, tt in enumerate(t_coords) if tt <= pt)
                    summary_rows.append({"Strato": strat, "Giorni": pt, "Persistenti": s_coords[idx]*100})
        st.dataframe(pd.DataFrame(summary_rows))

        chi2_stat, pval, k = logrank_multigroup(km_df["time"].to_numpy(), km_df["event"].to_numpy(), km_df[strat_col].to_numpy())
        st.subheader("📊 Test log-rank (multi-gruppo)")
        if np.isnan(chi2_stat):
            st.info("Test non calcolabile (eventi insufficienti o un solo gruppo)." )
        else:
            st.write(f"χ² = {chi2_stat:.3f} (df = {k-1}), p-value = {pval:.4g}")
else:
    st.info("Carica un file Excel per iniziare.")
